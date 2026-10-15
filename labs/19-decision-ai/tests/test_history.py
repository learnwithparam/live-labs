"""The refund history is the truth everything is graded on, so it must be the same file for every learner
and built the way a live system would see it."""

from datetime import date

from fennick.history import HISTORY, generate, is_fraud, load


def test_the_history_is_seeded_and_matches_the_file_in_data():
    rows = generate()
    assert rows == generate()
    on_disk = load(HISTORY)
    assert len(on_disk) == len(rows)
    assert [row["refund_id"] for row in on_disk[:50]] == [row["refund_id"] for row in rows[:50]]
    assert on_disk[100]["amount"] == str(rows[100]["amount"])


def test_refunds_in_the_last_30_days_are_counted_from_earlier_rows_only():
    rows = generate()
    by_buyer: dict[str, list[dict]] = {}
    for row in rows:
        by_buyer.setdefault(row["buyer_id"], []).append(row)
    for row in rows[::97]:
        day = date.fromisoformat(row["requested_at"])
        earlier = [other for other in by_buyer[row["buyer_id"]] if other["refund_id"] < row["refund_id"]
                   and (day - date.fromisoformat(other["requested_at"])).days <= 30]
        assert row["refunds_30d"] == len(earlier), row


def test_fraud_is_rare_and_found_after_the_refund():
    rows = generate()
    frauds = [row for row in rows if is_fraud(row)]
    assert 0.03 < len(frauds) / len(rows) < 0.08
    assert all(row["found_on"] > row["requested_at"] for row in frauds)
    assert all(row["found_on"] == "" for row in rows if not is_fraud(row))
