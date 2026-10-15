"""The audit log: append-only, hash-chained, in SQLite.

Each record carries the SHA-256 of the record before it, the way Certificate Transparency logs do. Change
one byte of an old reply, or delete a record, and every hash after it stops matching. Triggers stop an
honest mistake; the chain catches someone who goes around them with the file open.
"""

import hashlib
import hmac
import json
import sqlite3
from pathlib import Path

from opentelemetry.semconv._incubating.attributes import enduser_attributes, gen_ai_attributes

GENESIS = "0" * 64
SUBJECT = enduser_attributes.ENDUSER_PSEUDO_ID
SYSTEM = gen_ai_attributes.GEN_AI_AGENT_ID
CONVERSATION = gen_ai_attributes.GEN_AI_CONVERSATION_ID

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS audit_record (
    seq INTEGER PRIMARY KEY,
    record TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    -- Generated from the hashed JSON, so a query can never read a column the chain does not cover.
    at TEXT GENERATED ALWAYS AS (json_extract(record, '$.time')) VIRTUAL,
    system_id TEXT GENERATED ALWAYS AS (json_extract(record, '$."{SYSTEM}"')) VIRTUAL,
    subject TEXT GENERATED ALWAYS AS (json_extract(record, '$."{SUBJECT}"')) VIRTUAL,
    outcome TEXT GENERATED ALWAYS AS (json_extract(record, '$.outcome')) VIRTUAL
);
CREATE INDEX IF NOT EXISTS audit_by_subject ON audit_record (subject, at);
CREATE INDEX IF NOT EXISTS audit_by_system ON audit_record (system_id, at);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_record
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_record
BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
"""


def pseudonym(subject: str, key: bytes) -> str:
    """A keyed hash of a customer or staff number. The log can be read without revealing who is who."""
    return "p_" + hmac.new(key, subject.encode(), hashlib.sha256).hexdigest()[:20]


def canonical(record: dict) -> str:
    # Sorted keys and fixed separators: the same record always hashes the same, on any machine.
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def chain_hash(prev_hash: str, record_json: str) -> str:
    return hashlib.sha256(f"{prev_hash}\n{record_json}".encode()).hexdigest()


class AuditLog:
    """One SQLite file. `append` is the only write; nothing in this class can change a stored record."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def head(self) -> tuple[int, str]:
        row = self.connection.execute("SELECT seq, record_hash FROM audit_record ORDER BY seq DESC LIMIT 1").fetchone()
        return (row[0], row[1]) if row else (0, GENESIS)

    def append(self, record: dict) -> dict:
        """Link the record to the current head and store it, in one transaction so two writers cannot fork the chain."""
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            seq, prev_hash = self.head()
            record_json = canonical(record)
            record_hash = chain_hash(prev_hash, record_json)
            self.connection.execute("INSERT INTO audit_record (seq, record, prev_hash, record_hash) VALUES (?, ?, ?, ?)",
                                    (seq + 1, record_json, prev_hash, record_hash))
        return {"seq": seq + 1, "prev_hash": prev_hash, "record_hash": record_hash, **record}

    def query(self, where: str = "1", params: tuple = ()) -> list[dict]:
        rows = self.connection.execute(
            f"SELECT seq, record, prev_hash, record_hash FROM audit_record WHERE {where} ORDER BY seq", params).fetchall()
        return [{"seq": seq, "prev_hash": prev_hash, "record_hash": record_hash, **json.loads(record)}
                for seq, record, prev_hash, record_hash in rows]


def verify(path: Path, anchor: dict | None = None) -> dict:
    """Walk the chain from the first record. Report the first break, never skip past it.

    The chain alone cannot see the newest records being cut off, because what is left still links up.
    The anchor, the head a compliance officer signed, closes that gap: that record must still be there.
    """
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT seq, record, prev_hash, record_hash FROM audit_record ORDER BY seq").fetchall()
    finally:
        connection.close()
    expected_prev, expected_seq, hashes = GENESIS, 1, {}
    for seq, record_json, prev_hash, record_hash in rows:
        problem = None
        if seq != expected_seq:
            problem = f"record {expected_seq} is missing: the log jumps from {expected_seq - 1} to {seq}"
        elif prev_hash != expected_prev:
            problem = f"record {seq} does not point at record {seq - 1}'s hash"
        elif chain_hash(prev_hash, record_json) != record_hash:
            problem = f"record {seq} was changed after it was written: its hash no longer matches its content"
        if problem:
            return {"ok": False, "records": len(rows), "broken_at": min(seq, expected_seq), "problem": problem,
                    "head": None}
        hashes[seq] = record_hash
        expected_prev, expected_seq = record_hash, seq + 1
    if anchor and hashes.get(anchor["seq"]) != anchor["hash"]:
        return {"ok": False, "records": len(rows), "broken_at": anchor["seq"], "head": None,
                "problem": f"record {anchor['seq']}, signed off as the head, is gone or changed"}
    return {"ok": True, "records": len(rows), "broken_at": None, "problem": None,
            "head": {"seq": expected_seq - 1, "hash": expected_prev}}
