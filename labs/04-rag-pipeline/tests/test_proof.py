"""The lab's promise, against a real model: answers from section passages stand on their citation, more
of them than from fixed-size chunks, and a question the docs do not cover goes to a person, not a guess."""

from pathlib import Path

import pytest

from oncall.cli import client_and_model
from oncall.desk import Desk
from oncall.score import load_questions

LAB = Path(__file__).parent.parent
MIN_SHARE = 0.95  # of the answers given from section passages, supported by what they cite


@pytest.mark.proof
def test_section_passages_give_answers_that_stand_on_their_citation():
    client, model = client_and_model()
    questions = load_questions(LAB / "data/questions.jsonl")
    section = Desk(LAB / "data/docs", client, model, "section").run(questions)
    size = Desk(LAB / "data/docs", client, model, "size").run(questions)
    assert section["tally"]["share"] >= MIN_SHARE, f"{model}: {section['tally']}"
    assert section["tally"]["supported"] > size["tally"]["supported"], f"{model}: section {section['tally']}, size {size['tally']}"
    off = [row for row in section["rows"] if row["fact"] is None]
    assert all(row["answer"] is None for row in off), f"{model} guessed: {[row['answer'] for row in off]}"
