"""The lab's promise, on a real model and a real OpenFGA server: across every permission test pair, no
denied document reaches the model or its answer with either OpenFGA check, the prompt-only search leaks,
and allowed people still get their answers. Leaks are read from what the model was given, not what it said."""

import json
from pathlib import Path

import pytest

from copperline.cli import leaks, sync


@pytest.mark.proof
def test_openfga_leaks_nothing_and_the_prompt_only_search_leaks():
    assert sync() == 0
    assert leaks() == 0
    result = json.loads(Path("out/leaks.json").read_text())
    modes = result["score"]["modes"]
    assert modes["post"]["leaked"] == 0 and modes["pre"]["leaked"] == 0, modes
    assert modes["naive"]["leaked"] >= 1, modes
    # A search that refuses everyone would also leak nothing; the allowed side keeps it honest.
    assert modes["post"]["answered_allowed"] >= result["pairs"] - 2, modes
