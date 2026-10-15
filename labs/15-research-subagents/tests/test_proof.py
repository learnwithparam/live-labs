"""The lab's promise, on a real model: a lead with parallel subagents writes a brief with more claims
its sources support and more of the questions covered, and pays for it in tokens. Every verdict comes
from the quote check in code and a judge reading the cited source, never from the writer."""

import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.proof
def test_the_lead_and_its_subagents_cover_more_with_supported_claims_at_a_higher_cost():
    if not os.environ.get("LLM_API_KEY"):
        pytest.skip("needs LLM_API_KEY")
    for version in ("single", "lead"):
        subprocess.run(["research", "run", version], check=True)
    single, lead = (json.loads(Path(f"out/{version}.json").read_text()) for version in ("single", "lead"))
    # The judge is strict and runs vary: 17 of 23 and 17 of 20 in two of ours.
    assert lead["score"]["share"] >= 0.7, lead["score"]
    assert lead["score"]["supported"] > single["score"]["supported"], (single["score"], lead["score"])
    assert len(lead["score"]["covered"]) >= len(single["score"]["covered"]), (single["score"], lead["score"])
    # Breadth is bought with tokens: four context windows read more than one.
    assert lead["usage"]["research"]["tokens"] > 2 * single["usage"]["research"]["tokens"]
    assert single["usage"]["research"]["cost"] > 0 and lead["usage"]["research"]["cost"] > 0
