import http.client
import json
import threading
from http.server import ThreadingHTTPServer

from lucca.serve import Line, handler


def request(port: int, method: str, path: str, headers: dict | None = None, body: dict | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(method, path, body=json.dumps(body) if body else None, headers=headers or {})
    response = connection.getresponse()
    return response.status, response.read()


def test_the_display_answers_only_its_own_page(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(Line(tmp_path / "calls.json")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port
    try:
        status, body = request(port, "GET", "/api/state")
        state = json.loads(body)
        assert status == 200 and len(state["calls"]) == 8 and state["shift"] is None
        assert request(port, "GET", "/")[0] == 200
        # A page on another origin cannot start a call that spends model credit.
        assert request(port, "POST", "/api/call", {"Origin": "http://evil.example"}, {"id": "LB-01"})[0] == 403
        assert request(port, "GET", "/api/state", {"Host": "evil.example"})[0] == 403
        assert request(port, "POST", "/api/call", {}, {"id": "LB-99"})[0] == 404
    finally:
        server.shutdown()


def test_a_call_with_no_key_ends_with_the_error_instead_of_hanging(monkeypatch, tmp_path):
    import time

    import lucca.pipeline

    def no_key():
        raise SystemExit("LLM_API_KEY is not set. Copy env.example to .env and add your key.")

    monkeypatch.setattr(lucca.pipeline, "thinking", no_key)
    line = Line(tmp_path / "calls.json")
    call_id = line.play(line.state()["calls"][0] | {"keyed": {"staff": "unused"}})
    deadline = time.monotonic() + 30
    while not line.calls[call_id]["done"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert line.calls[call_id]["done"] and "LLM_API_KEY" in line.calls[call_id]["error"]
    assert line.busy is None
