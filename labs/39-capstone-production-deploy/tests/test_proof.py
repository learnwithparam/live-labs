"""The lab's promise, with numbers, on a real model: a burst load test against the naive and the hardened
service, then the ledger checked against the provider's own billing records. Needs LLM_API_KEY; the
load is about 120 short calls."""

import pytest

from brickhaven_api.billing import OpenRouterBilling
from brickhaven_api.config import LEDGER_TOLERANCE, P95_TARGET_MS, llm_settings
from brickhaven_api.ledger import reconcile
from brickhaven_api.loadtest import RUNAWAY_CLIENT, run_load
from brickhaven_api.serve import build_app, start_servers

# Cost per task must stay under this many dollars: a work order is a short call on a cheap model.
MAX_COST_PER_TASK = 0.001
MAX_ERROR_RATE = 0.02


def burst(naive: bool):
    app = build_app(naive=naive)
    api, portal = start_servers(app, 0, 0)
    try:
        report = run_load(f"http://127.0.0.1:{api.server_address[1]}", f"http://127.0.0.1:{portal.server_address[1]}",
                          total=60, concurrency=12, deadline_seconds=120)
    finally:
        api.shutdown(); portal.shutdown()
    return app, report


@pytest.mark.proof
def test_the_hardened_service_holds_its_numbers_under_burst_and_the_ledger_matches_billing():
    base_url, api_key = llm_settings()
    naive_app, naive = burst(naive=True)
    hardened_app, hardened = burst(naive=False)
    # The naive service serves the runaway client every call, so it spends the most and has no limit to show.
    assert naive.limited == 0 and sum(1 for outcome in naive.served if outcome.client == RUNAWAY_CLIENT) > 5
    assert hardened.total_cost < naive.total_cost
    # The hardened service refuses the runaway client with 429 and serves everyone else inside the signed p95.
    assert hardened.limited >= 20 and hardened.timed_out == 0
    assert hardened.error_rate <= MAX_ERROR_RATE
    assert 0 < hardened.p95_first_token_ms <= P95_TARGET_MS
    assert 0 < hardened.cost_per_task <= MAX_COST_PER_TASK
    # The truth outside the model: every ledger row has a provider billing record, and the totals agree.
    result = reconcile(hardened_app.ledger.rows, OpenRouterBilling(base_url, api_key).lookup)
    assert result.checked == len(hardened_app.ledger.rows) > 0 and not result.missing
    assert result.gap_share <= LEDGER_TOLERANCE and result.within_tolerance
