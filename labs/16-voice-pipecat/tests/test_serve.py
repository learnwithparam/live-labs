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
        assert {call["summary"] for call in state["calls"]} >= {"for staff: the family special is not on the menu"}
        assert request(port, "GET", "/")[0] == 200
        # A page on another origin cannot open the microphone line, which spends model credit.
        assert request(port, "POST", "/api/offer", {"Origin": "http://evil.example"}, {"sdp": "v=0", "type": "offer"})[0] == 403
        assert request(port, "GET", "/api/state", {"Host": "evil.example"})[0] == 403
        assert request(port, "POST", "/api/offer", {}, {"type": "offer"})[0] == 404
    finally:
        server.shutdown()


def test_a_live_call_with_no_key_is_refused_with_the_reason_before_the_line_opens(monkeypatch, tmp_path):
    from lucca import config

    def no_key():
        raise SystemExit("LLM_API_KEY is not set. Copy env.example to .env and add your key.")

    monkeypatch.setattr(config, "llm_settings", no_key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(Line(tmp_path / "calls.json")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, body = request(server.server_port, "POST", "/api/offer", {}, {"sdp": "v=0", "type": "offer"})
        assert status == 400 and "LLM_API_KEY" in json.loads(body)["error"]
    finally:
        server.shutdown()
