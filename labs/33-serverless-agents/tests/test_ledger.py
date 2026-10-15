"""The ledger's idempotency key is what makes a rerun safe: the same invoice written twice is one row."""

from meterline import ledger

ENTRY = {"customer_id": "MC-101", "invoice": "INV-2026-09-0101", "period": "2026-09", "total": 12505.14,
         "adjustment": 0.0, "approved_by": "agent"}


def test_a_repeat_write_with_the_key_is_a_no_op(tmp_path):
    state = tmp_path / "meterline.db"
    key = ledger.ledger_key("MC-101", "2026-09")
    first = ledger.write_invoice(state, ENTRY, key)
    second = ledger.write_invoice(state, {**ENTRY, "total": 1.0}, key)
    assert first["inserted"] and not second["inserted"]
    assert second["row_id"] == first["row_id"] and second["total"] == 12505.14
    assert len(ledger.rows(state, "select * from ledger")) == 1
    assert [row["inserted"] for row in ledger.rows(state, "select inserted from write_attempts")] == [1, 0]


def test_a_plain_insert_writes_the_invoice_twice(tmp_path):
    state = tmp_path / "naive.db"
    ledger.write_invoice(state, ENTRY, None)
    ledger.write_invoice(state, ENTRY, None)
    assert len(ledger.rows(state, "select * from ledger")) == 2


def test_the_hook_runs_after_the_row_commits(tmp_path):
    state, seen = tmp_path / "meterline.db", []
    ledger.after_commit_hooks.append(lambda customer_id: seen.append(len(ledger.rows(state, "select * from ledger"))))
    try:
        ledger.write_invoice(state, ENTRY, "MC-101:2026-09")
    finally:
        ledger.after_commit_hooks.clear()
    assert seen == [1]
