"""The chain makes an edit, a deletion or a cut-off tail visible to anyone who verifies."""

import sqlite3

import pytest

from larkspur.auditlog import chain_hash, verify
from larkspur.tamper import tampered_copy


def test_the_replayed_log_verifies(fortnight):
    status = verify(fortnight["db"])
    assert status["ok"] and status["records"] == len(fortnight["outcomes"]) == 27


def test_the_database_refuses_update_and_delete(fortnight):
    connection = sqlite3.connect(fortnight["db"])
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("UPDATE audit_record SET record = '{}' WHERE seq = 1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        connection.execute("DELETE FROM audit_record WHERE seq = 1")
    connection.close()


def test_an_edited_record_breaks_the_chain(fortnight, tmp_path):
    tampered_copy(fortnight["db"], tmp_path / "copy.sqlite", "edit", 4)
    status = verify(tmp_path / "copy.sqlite")
    assert not status["ok"] and status["broken_at"] == 4 and "changed" in status["problem"]


def test_a_deleted_record_breaks_the_chain(fortnight, tmp_path):
    tampered_copy(fortnight["db"], tmp_path / "copy.sqlite", "delete", 9)
    status = verify(tmp_path / "copy.sqlite")
    assert not status["ok"] and status["broken_at"] == 9 and "missing" in status["problem"]


def test_the_signed_anchor_catches_a_cut_off_tail(fortnight, tmp_path):
    head = verify(fortnight["db"])["head"]
    tampered_copy(fortnight["db"], tmp_path / "copy.sqlite", "delete", head["seq"])
    # Without the anchor the shorter log still links up; with it, the cut shows.
    assert verify(tmp_path / "copy.sqlite")["ok"]
    assert not verify(tmp_path / "copy.sqlite", head)["ok"]


def test_an_edited_record_with_a_fresh_hash_still_breaks_the_chain(fortnight, tmp_path):
    """An insider who also recomputes the edited record's own hash is caught by the next record's link."""
    copy = tmp_path / "copy.sqlite"
    tampered_copy(fortnight["db"], copy, "edit", 4)
    connection = sqlite3.connect(copy)
    record_json, prev_hash = connection.execute("SELECT record, prev_hash FROM audit_record WHERE seq = 4").fetchone()
    connection.execute("UPDATE audit_record SET record_hash = ? WHERE seq = 4", (chain_hash(prev_hash, record_json),))
    connection.commit()
    connection.close()
    status = verify(copy)
    assert not status["ok"] and status["broken_at"] == 5
