"""The finance page's server answers only this machine and accepts only decisions on open lines."""

import http.client
import json
import threading

import pytest

from quillmark.serve import Books, serve

REPORT = {"attribution": {"unattributed": [{"generation_id": "gen-open-1"}]},
          "customers": {"tidewell": "Tidewell Pet Co"}, "features": ["weekly-digest"]}


@pytest.fixture
def server():
    recorded = []
    books = Books(load=lambda: REPORT, decide=lambda generation, decision: recorded.append((generation, decision)) or REPORT)
    running = serve(books, 0)
    threading.Thread(target=running.serve_forever, daemon=True).start()
    yield running.server_port, recorded
    running.shutdown()
    running.server_close()


def request(port: int, method: str, route: str, body: dict | None = None, headers: dict | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port)
    connection.request(method, route, body=body and json.dumps(body),
                       headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    response = connection.getresponse()
    return response.status, response.read()


def test_another_site_cannot_read_the_company_s_costs(server):
    port, _ = server
    assert request(port, "GET", "/api/report", headers={"Origin": "https://evil.example"})[0] == 403
    assert request(port, "GET", "/api/report", headers={"Host": "quillmark.example"})[0] == 403


def test_the_page_and_the_report_are_served(server):
    port, _ = server
    status, page = request(port, "GET", "/")
    assert status == 200 and b'data-surface="month-end AI cost reconciliation' in page
    status, body = request(port, "GET", "/api/report")
    assert status == 200 and json.loads(body)["features"] == ["weekly-digest"]


def test_finance_can_only_decide_lines_that_are_open(server):
    port, recorded = server
    assert request(port, "POST", "/api/decide", {"generation_id": "gen-closed", "flag": True})[0] == 400
    assert request(port, "POST", "/api/decide", {"generation_id": "gen-open-1", "customer": "nobody",
                                                 "feature": "weekly-digest"})[0] == 400
    assert request(port, "POST", "/api/decide", {"generation_id": "gen-open-1", "customer": "tidewell",
                                                 "feature": "weekly-digest"})[0] == 200
    assert recorded == [("gen-open-1", {"customer": "tidewell", "feature": "weekly-digest"})]
