"""Every test reads and writes reviewer notes under a scratch folder, never the lab's own out/."""

import pytest

import brickhaven_rfc.review as review_module


@pytest.fixture(autouse=True)
def scratch_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(review_module, "SEND_BACKS", tmp_path / "send_backs.jsonl")
