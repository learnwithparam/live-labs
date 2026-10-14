"""Recall@k: of the questions a person labelled, how many have their labelled section in the top k.

Recall is measured on retrieval alone, before any answer is written: an answer cannot cite a section
the search never returned, so this is the number to raise first.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from wiki.search import WAYS, Search

KS = (1, 3, 5, 10)
# The top five go to the model in an answer. A labelled section below five is a miss for the product.
SHOWN = 5


@dataclass
class Label:
    question: str
    passage: str


def load_golden(path: Path) -> list[Label]:
    return [Label(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def rank_of(search: Search, label: Label, how: str) -> int | None:
    """Where the labelled section came back, 1 for the top, or None when it is not in the top 10."""
    ids = [hit.passage.id for hit in search.find(label.question, how, max(KS))]
    return ids.index(label.passage) + 1 if label.passage in ids else None


def recall(ranks: list[int | None], k: int) -> int:
    return sum(1 for r in ranks if r is not None and r <= k)


def evaluate(search: Search, golden: list[Label], ways: tuple[str, ...] = WAYS) -> dict:
    ranks = {how: [rank_of(search, label, how) for label in golden] for how in ways}
    best = ways[-1]
    rows = [{"question": label.question, "passage": label.passage, "ranks": {how: ranks[how][i] for how in ways}} for i, label in enumerate(golden)]
    # Still outside the top five on the best way: no search setting fixes these, so a person reads the
    # question and the passages that came back, and decides whether the label or the runbook is wrong.
    person = [row for row in rows if row["ranks"][best] is None or row["ranks"][best] > SHOWN]
    return {
        "total": len(golden),
        "recall": {how: {k: recall(ranks[how], k) for k in KS} for how in ways},
        "rows": rows,
        "person": [row["question"] for row in person],
    }
