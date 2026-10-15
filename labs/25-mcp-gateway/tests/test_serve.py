"""The console answers only its own page: a foreign page could otherwise approve a server."""

import threading
import urllib.error
import urllib.request

import pytest

from basalt import serve


class StubConsole:
    def state(self) -> dict:
        return {"servers": []}


@pytest.fixture()
def console_url():
    server = serve.serve(StubConsole(), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def status(url: str, headers: dict) -> int:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_the_page_and_state_load_locally(console_url):
    assert status(console_url + "/", {}) == 200
    assert status(console_url + "/api/state", {}) == 200


def test_a_foreign_origin_or_host_is_refused(console_url):
    assert status(console_url + "/api/state", {"Origin": "http://evil.example"}) == 403
    assert status(console_url + "/api/state", {"Host": "evil.example"}) == 403


def test_the_page_writes_model_text_with_text_content_only():
    page = serve.PAGE.read_text()
    assert "innerHTML" not in page
    assert 'data-surface="MCP server approval console"' in page
