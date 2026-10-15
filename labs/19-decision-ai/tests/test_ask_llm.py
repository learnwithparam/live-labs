"""The naive baseline, scripted: batches, a skipped id, an invented id, and the billed cost."""

import asyncio

from fakes import FakeScorer

from fennick import ask_llm, history, model


def test_every_sampled_refund_gets_a_score_and_a_skipped_one_counts_as_a_coin_toss():
    rows = ask_llm.sample(model.window(history.load(), "test"), size=60)
    scorer = FakeScorer(chance=12, skip=rows[3]["refund_id"], invent="FM-999999")
    asked = asyncio.run(ask_llm.score_all(scorer, "openai/gpt-6-luna", rows))
    assert len(scorer.requests) == asked["calls"] == 3
    assert set(asked["scores"]) == {row["refund_id"] for row in rows}
    assert asked["scores"][rows[3]["refund_id"]] == 50 and asked["missing"] == [rows[3]["refund_id"]]
    assert asked["scores"][rows[0]["refund_id"]] == 12
    assert asked["usage"]["cost"] == 0.0007 * 3


def test_the_model_sees_the_features_but_never_the_outcome():
    row = model.window(history.load(), "test")[0]
    shown = ask_llm.describe(row)
    assert f"refund_id={row['refund_id']}" in shown and "delivery_scan=" in shown
    assert "found" not in shown and row["buyer_id"] not in shown


def test_the_sample_is_seeded():
    rows = model.window(history.load(), "test")
    assert ask_llm.sample(rows) == ask_llm.sample(rows)
