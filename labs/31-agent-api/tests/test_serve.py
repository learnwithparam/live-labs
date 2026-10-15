"""The page server: local only, signs tokens for known people, and records the operator's decision."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from mosswood import gateway, serve


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "LIMITS_FILE", tmp_path / "limits.json")
    monkeypatch.setattr(serve, "LOAD_SUMMARY", tmp_path / "none.json")
    log = tmp_path / "access.log"
    log.write_text(json.dumps({"at": "1.0", "method": "POST", "status": 429, "tenant": "ninebark", "user": "lena.fischer",
                               "limit_scope": "tenant", "upstream": None, "first_byte_ms": None, "flags": "RL"}) + "\n")
    reloads = []
    running = serve.serve(serve.Operator(reload=lambda: reloads.append(1) or "", log=log), 0)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{running.server_port}", reloads
    running.shutdown()


def call(url: str, body: dict | None = None, origin: str | None = None) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_another_site_cannot_get_a_token(server):
    base, _ = server
    assert call(f"{base}/api/token", {"tenant": "harlow", "user": "amara.osei"}, origin="https://evil.example")[0] == 403
    status, body = call(f"{base}/api/token", {"tenant": "harlow", "user": "amara.osei"})
    assert status == 200 and json.loads(body)["token"].count(".") == 2
    assert call(f"{base}/api/token", {"tenant": "harlow", "user": "priya.raman"})[0] == 400


def test_the_operator_sees_the_tenant_at_its_limit_and_raises_it(server):
    base, reloads = server
    state = json.loads(call(f"{base}/api/gateway")[1])
    assert [case["tenant"] for case in state["summary"]["needs_operator"]] == ["ninebark"]
    assert call(f"{base}/api/limit", {"tenant": "ninebark", "limit": 5000})[0] == 400
    status, body = call(f"{base}/api/limit", {"tenant": "ninebark", "limit": 12, "reason": "month-end"})
    assert status == 200 and json.loads(body)["limits"]["ninebark"] == 12 and reloads == [1]
