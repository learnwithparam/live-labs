"""The egress proxy, in process: the real key is added here, the model is pinned, and budgets bite."""

import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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


def test_concurrent_calls_cannot_spend_past_the_budget():
    # Each call costs $0.02 and takes a moment upstream. A budget checked before the call and charged after
    # it would let all eight through at once; only two fit in $0.03 once spend is settled call by call.
    def slow_upstream(body: bytes) -> tuple[int, bytes]:
        time.sleep(0.2)
        return 200, json.dumps({"choices": [], "usage": {"cost": 0.02}}).encode()

    ledger = Ledger((0.10, 0.01, 0.50))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(ledger, "http://upstream", REAL_KEY, "m", ADMIN,
                                                           slow_upstream))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    call(url, "/admin/sessions", {"token": "tok-5", "session": "FH-105", "budget": 0.03}, admin=ADMIN)
    with ThreadPoolExecutor(8) as pool:
        statuses = list(pool.map(lambda _: call(url, "/v1/chat/completions", {"messages": []}, token="tok-5")[0],
                                 range(8)))
    server.shutdown()
    assert sorted(statuses) == [200, 200] + [402] * 6
    assert ledger.report()["FH-105"]["spent"] == pytest.approx(0.04)


def test_only_the_fields_the_agent_needs_reach_the_model_api(proxy):
    # OpenRouter reads more than `model`: `models` picks fallback models, `plugins` can fetch the web, and `n`
    # multiplies the bill. None of those may ride along on a chat completion.
    url, _, sent = proxy
    call(url, "/admin/sessions", {"token": "tok-6", "session": "FH-106", "budget": 0.05}, admin=ADMIN)
    status, _ = call(url, "/v1/chat/completions", token="tok-6", body={
        "messages": [], "tools": [], "tool_choice": "auto", "models": ["openai/most-expensive"],
        "plugins": [{"id": "web"}], "n": 50, "provider": {"order": ["somewhere"]}})
    assert status == 200
    assert sorted(sent[0]) == ["messages", "model", "tool_choice", "tools", "usage"]


@pytest.mark.parametrize("smuggled", [
    {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://attacker.example/x"}}]}]},
    {"messages": [], "tools": [{"type": "openrouter:web_search"}]},
    {"messages": [{"role": "user", "content": "hi", "annotations": [{"url": "https://attacker.example"}]}]},
    {"messages": [], "tool_choice": {"type": "web_search"}},
])
def test_a_request_shaped_unlike_the_agents_never_leaves(proxy, smuggled):
    # Inside the allowed fields a provider still fetches image URLs and runs its own server tools.
    url, _, sent = proxy
    call(url, "/admin/sessions", {"token": "tok-7", "session": "FH-107", "budget": 0.05}, admin=ADMIN)
    status, _ = call(url, "/v1/chat/completions", token="tok-7", body=smuggled)
    assert status == 403
    assert sent == []
