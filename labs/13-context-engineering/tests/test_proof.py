"""The lab's promise, on a real model and the provider's own bill: the engineered context audits the
whole month right, most of every request comes from the cache, and it costs less than the naive one."""

import json
from pathlib import Path

import pytest

from halyard.cli import review_all


@pytest.mark.proof
def test_the_engineered_context_completes_the_audit_cheaper_from_the_cache():
    assert review_all() == 0
    results = json.loads(Path("out/review.json").read_text())
    naive, engineered = results["naive"]["score"], results["engineered"]["score"]
    assert engineered["complete"], [row for row in engineered["rows"] if not row["right"]]
    assert engineered["billed"], "OpenRouter sends the billed cost; without it the cost is a list-price estimate"
    assert engineered["hit_rate"] >= 0.7, engineered["hit_rate"]
    assert naive["hit_rate"] < 0.3, naive["hit_rate"]
    assert engineered["cost"] < naive["cost"], (naive["cost"], engineered["cost"])
