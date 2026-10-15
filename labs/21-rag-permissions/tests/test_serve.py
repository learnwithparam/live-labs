"""The page's server answers only this machine, and marks each source the viewer could or could not open."""

import http.client
import threading

import pytest
from fakes import FakeAuthorizer, FakeOpenAI, cite_everything

from copperline import serve as serve_module
from copperline.serve import Search, serve
from copperline.sync import build, documents

EVERY_DOCUMENT = {document["id"] for document in documents()}


@pytest.fixture
def server(tmp_path, monkeypatch):
    # An admin decision saved by an earlier run would release the held ticket; these tests start clean.
    monkeypatch.setattr(serve_module, "build", lambda: build(admin_decisions={}))
    table = FakeAuthorizer({"ravi": EVERY_DOCUMENT - {"drv-board-pack-q3"}, "maya": EVERY_DOCUMENT})
    search = Search(lambda: (FakeOpenAI(cite_everything), "fake"), table, leaks_file=tmp_path / "none.json")
    running = serve(search, 0)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield running.server_port
    running.shutdown()
    running.server_close()


def request(port: int, method: str, path: str, body: str | None = None, headers: dict | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port)
    connection.request(method, path, body=body, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    response = connection.getresponse()
    return response.status, response.read()


def test_another_site_cannot_ask_as_an_employee(server):
    status, _ = request(server, "POST", "/api/ask", '{"user": "maya", "question": "bonus", "mode": "post"}',
                        {"Origin": "https://evil.example"})
    assert status == 403
    status, _ = request(server, "GET", "/api/state", headers={"Host": "copperline.example"})
    assert status == 403


def test_the_state_carries_people_and_the_held_ticket_title(server):
    import json

    status, body = request(server, "GET", "/api/state")
    state = json.loads(body)
    assert status == 200 and state["leaks"] is None
    assert state["held"][0]["title"].startswith("SEC-61")


def test_naive_mode_shows_the_source_the_viewer_could_not_open(server):
    import json

    question = '{"user": "ravi", "question": "What bonus pool does the Q3 board pack ask the board to approve?", '
    status, body = request(server, "POST", "/api/ask", question + '"mode": "naive"}')
    sources = {source["document"]: source for source in json.loads(body)["sources"]}
    assert status == 200 and sources["drv-board-pack-q3"]["allowed"] is False
    status, body = request(server, "POST", "/api/ask", question + '"mode": "post"}')
    result = json.loads(body)
    assert "drv-board-pack-q3" not in {source["document"] for source in result["sources"]}
    assert all(source["why"] for source in result["sources"])
