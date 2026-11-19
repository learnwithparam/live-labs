"""The ledger's arithmetic, and the reconcile step against a fake billing record that can drift or forget."""

import pytest

from brickhaven_api.config import FALLBACK_MODEL, PRIMARY_MODEL
from brickhaven_api.ledger import Ledger, price_call, reconcile
from brickhaven_api.provider import Completion


def completion(model=PRIMARY_MODEL, generation_id="gen-1"):
    return Completion("{}", generation_id, model, 1_000_000, 500_000, 100.0)


def test_a_call_is_costed_from_list_prices_for_its_own_model():
    assert price_call(PRIMARY_MODEL, 1_000_000, 500_000) == pytest.approx(0.07 + 0.10)
    assert price_call(FALLBACK_MODEL, 1_000_000, 500_000) == pytest.approx(0.10 + 0.25)
    with pytest.raises(KeyError):
        price_call("unpriced/model", 1, 1)


def test_rows_are_saved_and_read_back_by_a_new_ledger(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.record("maple-portal", "primary", completion(generation_id="gen-1"))
    ledger.record("harbor-sms", "fallback", completion(FALLBACK_MODEL, "gen-2"))
    again = Ledger(tmp_path / "ledger.jsonl")
    assert [row["generation_id"] for row in again.rows] == ["gen-1", "gen-2"]
    assert again.cost_by_client()["harbor-sms"] == pytest.approx(0.35)
    assert again.total() == pytest.approx(0.17 + 0.35)


def rows_with_billing(tmp_path, **kwargs):
    from fakes import FakeBilling
    ledger = Ledger()
    for number in range(5):
        ledger.record("maple-portal", "primary", completion(generation_id=f"gen-{number}"))
    return ledger.rows, FakeBilling(ledger.rows, **kwargs)


def test_a_ledger_that_matches_billing_is_within_tolerance(tmp_path):
    rows, billing = rows_with_billing(tmp_path)
    result = reconcile(rows, billing.lookup)
    assert result.checked == 5 and result.within_tolerance and result.gap_share == pytest.approx(0)


def test_a_small_drift_passes_and_a_large_one_fails(tmp_path):
    rows, billing = rows_with_billing(tmp_path, skew=1.03)
    assert reconcile(rows, billing.lookup).within_tolerance
    rows, billing = rows_with_billing(tmp_path, skew=1.2)
    result = reconcile(rows, billing.lookup)
    assert not result.within_tolerance and len(result.mismatched) == 5


def test_a_generation_the_provider_never_billed_fails_the_check(tmp_path):
    rows, billing = rows_with_billing(tmp_path, forget={"gen-3"})
    result = reconcile(rows, billing.lookup)
    assert result.missing == ["gen-3"] and not result.within_tolerance


def test_an_empty_ledger_proves_nothing(tmp_path):
    assert not reconcile([], lambda generation_id: None).within_tolerance
