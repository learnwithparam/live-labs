"""The lab's promise, against a real model: the shipped prompt holds the approved fields, the golden set
blocks the shorter prompt and names what it broke, and it passes the years rule that only fixes."""

from pathlib import Path

import pytest

from postings.cli import client_and_model
from postings.extract import read_all
from postings.score import gate, load_golden

LAB = Path(__file__).parent.parent
MIN_RIGHT = 13  # of 14 postings, for every field under the shipped prompt


@pytest.mark.proof
def test_the_golden_set_blocks_the_shorter_prompt_and_passes_the_fix():
    client, model = client_and_model()
    golden = load_golden(LAB / "data/golden.jsonl")
    postings = {name: (LAB / "data/postings" / name).read_text() for name in golden}

    def run(stem: str) -> dict:
        prompt = (LAB / "prompts" / f"{stem}.txt").read_text()
        return {name: reading.fields for name, reading in read_all(postings, prompt, client, model).items()}

    shipped, shorter, years = run("shipped"), run("shorter"), run("years-rule")
    weak = {field: right for field, right in gate(golden, shipped, shipped).before.items() if right < MIN_RIGHT}
    assert not weak, f"{model} under the shipped prompt: {weak}"
    blocked = gate(golden, shipped, shorter)
    assert blocked.blocked and blocked.needs_person, f"{model}: the shorter prompt was not caught"
    fixed = gate(golden, shipped, years)
    assert not fixed.blocked, f"{model}: the years rule made {fixed.worse} worse"
    assert fixed.after["seniority"] == len(golden), f"{model}: years rule seniority {fixed.after['seniority']}/{len(golden)}"
