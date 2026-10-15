"""The egress proxy, in process: the real key is added here, the model is pinned, and budgets bite."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from fernhill.egress import Ledger, handler

ADMIN = "admin-secret"
REAL_KEY = "sk-real-upstream-key"


@pytest.fixture
def proxy():
    sent = []

    def send_upstream(body: bytes) -> tuple[int, bytes]:
        sent.append(json.loads(body))
        return 200, json.dumps({"choices": [], "usage": {"prompt_tokens": 1000, "completion_tokens": 100,
                                                          "cost": 0.02}}).encode()

    ledger = Ledger((0.10, 0.01, 0.50))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(ledger, "http://upstream", REAL_KEY, "openai/gpt-6-luna",
                                                           ADMIN, send_upstream))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", ledger, sent
    server.shutdown()


def call(url: str, path: str, body: dict | None = None, token: str = "", admin: str = "") -> tuple[int, dict]:
    request = urllib.request.Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}", "X-Admin-Token": admin})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_proxy_pins_the_model_and_asks_for_the_bill(proxy):
    url, ledger, sent = proxy
    call(url, "/admin/sessions", {"token": "tok-1", "session": "FH-101", "budget": 0.05}, admin=ADMIN)
    status, _ = call(url, "/v1/chat/completions", {"model": "openai/most-expensive", "messages": []}, token="tok-1")
    assert status == 200
    assert sent[0]["model"] == "openai/gpt-6-luna" and sent[0]["usage"] == {"include": True}
    assert ledger.report()["FH-101"]["spent"] == pytest.approx(0.02)


def test_a_spent_budget_answers_402_and_is_recorded(proxy):
    url, ledger, _ = proxy
    call(url, "/admin/sessions", {"token": "tok-2", "session": "FH-102", "budget": 0.03}, admin=ADMIN)
    statuses = [call(url, "/v1/chat/completions", {"messages": []}, token="tok-2")[0] for _ in range(3)]
    assert statuses == [200, 200, 402]
    assert ledger.report()["FH-102"]["refused"] == ["cost budget $0.03 spent"]


def test_anything_but_chat_completions_is_denied_and_unknown_tokens_get_nothing(proxy):
    url, ledger, sent = proxy
    call(url, "/admin/sessions", {"token": "tok-3", "session": "FH-103", "budget": 0.05}, admin=ADMIN)
    assert call(url, "/v1/models", token="tok-3")[0] == 403
    assert call(url, "/v1/embeddings", {"input": "x"}, token="tok-3")[0] == 403
    assert call(url, "/v1/chat/completions", {"messages": []}, token="made-up")[0] == 401
    assert sent == []
    assert len(ledger.report()["FH-103"]["refused"]) == 2


def test_only_the_orchestrator_opens_sessions_or_reads_the_ledger(proxy):
    url, _, _ = proxy
    assert call(url, "/admin/sessions", {"token": "mine", "session": "x", "budget": 99}, admin="guess")[0] == 403
    assert call(url, "/admin/ledger", admin="guess")[0] == 403
    assert call(url, "/admin/ledger", admin=ADMIN) == (200, {})


def test_list_prices_cost_a_call_when_the_provider_sends_no_bill():
    ledger = Ledger((0.10, 0.01, 0.50))
    usage = {"prompt_tokens": 2000, "completion_tokens": 400, "prompt_tokens_details": {"cached_tokens": 1000}}
    assert ledger.cost_of(usage) == pytest.approx((1000 * 0.10 + 1000 * 0.01 + 400 * 0.50) / 1e6)
