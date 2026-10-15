"""The lab's promise on a real model: every audit request answered from the inventory and log, and no edit unseen.

Run with `uv run pytest -m proof -s` and LLM_API_KEY set. It replays the fortnight once, about two cents.
"""

import pytest
from conftest import KEY

from larkspur import inventory, naive
from larkspur.audit import Desk, answer_all, requests_of
from larkspur.auditlog import AuditLog, verify
from larkspur.cli import model_client
from larkspur.config import DATA
from larkspur.services import replay
from larkspur.tamper import tampered_copy

pytestmark = pytest.mark.proof


def test_the_auditor_gets_every_answer_from_the_inventory_and_log(tmp_path):
    systems = inventory.load()
    log = AuditLog(tmp_path / "audit.sqlite")
    outcomes = replay(DATA, systems, model_client(), log, KEY, tmp_path / "app-logs")
    statuses = [outcome.status for outcome in outcomes]
    assert "failed" not in statuses, [outcome.reason for outcome in outcomes if outcome.status == "failed"]
    # The two promo-writer calls, four mailer calls and the unlisted model are refused before any token is spent.
    assert statuses.count("refused") == 7

    for outcome in outcomes:
        record = outcome.record
        if outcome.status == "answered" and record["talks_to_customers"]:
            assert record["disclosure.shown"] and outcome.delivered.startswith(systems[record["gen_ai.agent.id"]].disclosure)

    requests = requests_of(DATA)
    governed = answer_all(Desk(systems, log, KEY), requests)
    grepped = naive.answer_all(tmp_path / "app-logs", requests)
    log.close()
    print(f"\ngoverned {governed['answered']}/{governed['total']} median {governed['median_seconds'] * 1000:.1f} ms; "
          f"grep {grepped['answered']}/{grepped['total']} after {grepped['steps']} steps, {grepped['lines_read']} lines; "
          f"cost ${sum(outcome.record.get('cost.usd', 0.0) for outcome in outcomes):.4f}")
    assert governed["answered"] == governed["total"] == 14
    assert grepped["answered"] < governed["answered"]

    assert verify(tmp_path / "audit.sqlite")["ok"]
    for mode, seq in (("edit", 4), ("delete", 9)):
        tampered_copy(tmp_path / "audit.sqlite", tmp_path / f"{mode}.sqlite", mode, seq)
        assert verify(tmp_path / f"{mode}.sqlite")["broken_at"] == seq
