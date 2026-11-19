"""Keyless tests of the review page's API: local only, the draft refused and the RFC approved, and a
send-back that is refused without a reason and shown once recorded."""

import json
import threading
import urllib.error
import urllib.request

import pytest

import brickhaven_rfc.serve as page_server


@pytest.fixture
def board_url():
    server = page_server.serve(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def request(url: str, body: dict | None = None, origin: str | None = None) -> tuple[int, dict | str]:
    headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers)) as response:
            return response.status, json.loads(response.read()) if response.headers["Content-Type"] == "application/json" else response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_page_and_both_versions_load(board_url):
    status, page = request(board_url + "/")
    assert status == 200 and 'data-surface="architecture RFC review with sign-off"' in page
    _, draft = request(board_url + "/api/review?file=draft.md")
    _, rfc = request(board_url + "/api/review?file=rfc.toml")
    assert not draft["approved"] and rfc["approved"]
    assert sum(not mode["measured"] for mode in draft["modes"]) == 5
    assert request(board_url + "/api/review?file=../data/signoff.toml")[0] == 404


def test_a_send_back_needs_a_reason_and_then_blocks_that_version(board_url):
    note = {"file": "rfc.toml", "mode": "missed-hazard", "reviewer": "Dana Okafor", "reason": "ok"}
    assert request(board_url + "/api/send-back", note)[0] == 400
    status, after = request(board_url + "/api/send-back", note | {"reason": "Who labelled the gas cases is not named"})
    assert status == 200 and not after["approved"]
    assert [mode["id"] for mode in after["modes"] if not mode["measured"]] == ["missed-hazard"]


def test_another_site_cannot_send_a_failure_mode_back(board_url):
    note = {"file": "rfc.toml", "mode": "missed-hazard", "reviewer": "Mallory", "reason": "a reason with words"}
    assert request(board_url + "/api/send-back", note, origin="https://evil.example")[0] == 403
