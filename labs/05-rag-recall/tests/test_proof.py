"""The lab's promise, on the real models: hybrid search finds more labelled sections than either way alone,
and the reranker puts more of them first."""

from pathlib import Path

import pytest

LAB = Path(__file__).parent.parent
MIN_RECALL_AT_5 = 0.95  # of the labelled questions, on hybrid + rerank


@pytest.mark.proof
def test_hybrid_and_rerank_raise_recall_on_the_golden_set():
    from wiki.encoders import FastEmbed
    from wiki.ingest import ingest
    from wiki.score import evaluate, load_golden
    from wiki.search import Search

    result = evaluate(Search(ingest(LAB / "data/docs"), FastEmbed()), load_golden(LAB / "data/golden.jsonl"))
    r, total = result["recall"], result["total"]
    assert r["rerank"][5] >= MIN_RECALL_AT_5 * total, r
    assert r["hybrid"][5] >= max(r["keyword"][5], r["embedding"][5]), r
    assert r["rerank"][1] > r["hybrid"][1], r
