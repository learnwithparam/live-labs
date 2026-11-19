"""The two listeners over real sockets, and the load generator run against them with a fake provider."""

import json
import urllib.error
import urllib.request

import pytest

from brickhaven_api.config import PRIMARY_MODEL
from brickhaven_api.loadtest import RUNAWAY_CLIENT, Outcome, LoadReport, demo_keys, percentile, run_load, traffic_plan
from brickhaven_api.serve import start_servers
from fakes import FakeProvider


@pytest.fixture
def servers(make_app):
    started = []

    def start(provider=None, hardened=True):
        app = make_app(provider, hardened)
        api, portal = start_servers(app, 0, 0)
        started.extend([api, portal])
        return app, f"http://127.0.0.1:{api.server_address[1]}", f"http://127.0.0.1:{portal.server_address[1]}"
    yield start
    for server in started:
        server.shutdown()
        server.server_close()


def post(url, payload, headers=None):
    request = urllib.request.Request(url, json.dumps(payload).encode(), headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.load(response), response.headers
    except urllib.error.HTTPError as error:
        return error.code, json.load(error), error.headers


def test_the_api_answers_over_http_with_the_right_headers(servers):
    _, api, _ = servers()
    key = demo_keys()["runaway-sync"]
    statuses = [post(f"{api}/v1/work-orders", {"message": "tap drips"}, {"Authorization": f"Bearer {key}"}) for _ in range(4)]
    assert [status for status, _, _ in statuses] == [200, 200, 200, 429]
    assert statuses[3][2]["Retry-After"] and statuses[3][2]["X-Request-Id"]
    assert post(f"{api}/v1/work-orders", {"message": "x"})[0] == 401


def test_the_portal_is_not_on_the_api_port(servers):
    _, api, portal = servers()
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(f"{api}/api/summary")
    assert refused.value.code == 404
    summary = json.load(urllib.request.urlopen(f"{portal}/api/summary"))
    assert summary["mode"] == "hardened" and PRIMARY_MODEL not in json.dumps(summary)


def test_a_page_on_another_site_cannot_raise_a_limit(servers):
    app, _, portal = servers()
    body = {"operator": "Dana Okafor", "client": "maple-portal", "per_minute": 60, "reason": "move in week is busy"}
    assert post(f"{portal}/api/limit", body, {"Origin": "https://evil.example"})[0] == 403
    assert post(f"{portal}/api/limit", body, {"Origin": "http://127.0.0.1:8372"})[0] == 200
    assert app.keys.by_name("maple-portal").per_minute == 60
    assert post(f"{portal}/api/limit", {**body, "reason": "no"}, {})[0] == 400


def test_the_portal_page_is_served(servers):
    _, _, portal = servers()
    assert b"data-surface" in urllib.request.urlopen(f"{portal}/").read()


def test_the_percentile_is_nearest_rank():
    assert percentile([], 0.95) == 0.0
    assert percentile(list(range(1, 101)), 0.95) == 95
    assert percentile([7], 0.95) == 7


def test_the_traffic_plan_makes_half_the_calls_the_runaway_clients():
    plan = traffic_plan(60)
    assert plan.count(RUNAWAY_CLIENT) == 30 and plan.count("maple-portal") == plan.count("harbor-sms") == 15


def test_the_hardened_load_test_limits_the_runaway_client_and_serves_the_rest(servers):
    app, api, portal = servers()
    report = run_load(api, portal, total=60, concurrency=8, deadline_seconds=30)
    assert report.mode == "hardened" and report.errors == 0 and report.error_rate == 0
    assert report.limited >= 25 and len(report.served) + report.limited == 60
    assert sum(1 for outcome in report.served if outcome.client == RUNAWAY_CLIENT) <= 5
    assert report.p95_first_token_ms == 120 and report.cost_per_task == pytest.approx(app.ledger.total() / len(report.served))


def test_the_naive_load_test_serves_every_call_and_spends_more(servers):
    hardened_app, hardened_api, hardened_portal = servers()
    naive_app, naive_api, naive_portal = servers(hardened=False)
    hardened = run_load(hardened_api, hardened_portal, 60, 8, 30)
    naive = run_load(naive_api, naive_portal, 60, 8, 30)
    assert naive.mode == "naive" and len(naive.served) == 60 and naive.total_cost > hardened.total_cost


def test_a_dead_primary_shows_as_fallback_calls_in_the_report(servers):
    _, api, portal = servers(FakeProvider(failing={PRIMARY_MODEL}))
    report = run_load(api, portal, total=12, concurrency=4, deadline_seconds=30)
    assert report.served_by("fallback") == len(report.served) > 0 and report.breakers["primary"] == "open"


def test_calls_that_never_finish_are_errors_and_the_deadline_is_hard(servers):
    import threading
    release = threading.Event()

    class Stuck(FakeProvider):
        def complete(self, model, system, user):
            release.wait(10)
            return super().complete(model, system, user)
    _, api, portal = servers(Stuck())
    try:
        report = run_load(api, portal, total=6, concurrency=2, deadline_seconds=0.5)
    finally:
        release.set()
    assert report.timed_out > 0 and report.errors >= report.timed_out


def test_error_rate_counts_only_what_a_person_would_call_a_failure():
    outcomes = [Outcome("maple-portal", 200, 100.0, "primary"), Outcome(RUNAWAY_CLIENT, 429), Outcome("harbor-sms", 429), Outcome("maple-portal", 503)]
    report = LoadReport("hardened", 4, outcomes, 0, 0.0)
    assert report.limited == 1 and report.errors == 2 and report.error_rate == 0.5
