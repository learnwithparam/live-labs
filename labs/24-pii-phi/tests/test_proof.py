"""The lab's promise, on a real model: every note is written from the redacted transcript, no annotated
identifier appears in the prompt or the stored note, the Spanish visit is held, and Presidio with the
clinic's recognizers misses fewer identifiers than regex. The truth is the person's annotations."""

import json
from pathlib import Path

import pytest

from wrenfield.cli import grade_all, write_notes
from wrenfield.grade import visits
from wrenfield.scribe import leaked_phi


@pytest.mark.proof
def test_no_annotated_phi_reaches_the_real_model_and_presidio_beats_regex():
    assert write_notes() == 0
    records = json.loads(Path("out/notes.json").read_text())["records"]
    for one in visits():
        record = records[one["id"]]
        if one["language"] != "en":
            assert record["status"] == "held" and record["messages"] == []
            continue
        sent = [message["content"] for message in record["messages"]] + [record["note"]]
        assert record["note"], one["id"]
        assert leaked_phi(one, sent) == [], one["id"]
    assert grade_all() == 0
    graded = json.loads(Path("out/grade.json").read_text())
    assert graded["presidio"]["missed"] < graded["regex"]["missed"]
    assert graded["presidio"]["recall"] > graded["regex"]["recall"]
