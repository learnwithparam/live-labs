"""The truth outside the model: Meterline's invoice ledger, plus the logs that let the proof count work.

The ledger holds one corrected total per customer per period. Every other table here is evidence:
each write attempt, each paid model call, each function invocation and each item waiting for finance.
"""

import os
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

SCHEMA = """
create table if not exists ledger (
    row_id integer primary key,
    idempotency_key text unique,
    customer_id text not null,
    invoice text not null,
    period text not null,
    total real not null,
    adjustment real not null,
    approved_by text not null,
    written_at real not null
);
create table if not exists write_attempts (
    customer_id text, idempotency_key text, process_id integer, inserted integer, attempted_at real
);
create table if not exists model_calls (
    call_id integer primary key, customer_id text, attempt integer, process_id integer,
    started_at real, finished_at real, cost real
);
create table if not exists invocations (
    invocation_id integer primary key, execution text, customer_id text, process_id integer,
    started_at real, finished_at real
);
create table if not exists review_queue (
    customer_id text primary key, callback_id text, reasons text, proposed_total real, draft_total real,
    adjustment real, status text, decided_by text, queued_at real
);
"""

# Called after a ledger row commits and before the step can checkpoint. The host's crash drill hangs off
# this, so a kill can land in the one gap an idempotency key exists to cover.
after_commit_hooks: list[Callable[[str], None]] = []


def connect(path: Path) -> sqlite3.Connection:
    """A connection per use: the host's threads, the batch and the page all touch the file at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("pragma journal_mode=wal")
    connection.executescript(SCHEMA)
    return connection


def ledger_key(customer_id: str, period: str) -> str:
    """One invoice per customer per period, so the same job written twice lands on the same key."""
    return f"{customer_id}:{period}"


def write_invoice(path: Path, entry: dict, idempotency_key: str | None) -> dict:
    """Insert one ledger row. With a key, a repeat is a no-op that returns the row already there.

    The naive loop passes no key, which is how most first versions are written: a plain insert.
    """
    with closing(connect(path)) as connection:
        cursor = connection.execute(
            "insert into ledger (idempotency_key, customer_id, invoice, period, total, adjustment, approved_by, written_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?) on conflict (idempotency_key) do nothing",
            (idempotency_key, entry["customer_id"], entry["invoice"], entry["period"], entry["total"],
             entry["adjustment"], entry["approved_by"], time.time()))
        inserted = cursor.rowcount == 1
        connection.execute("insert into write_attempts values (?, ?, ?, ?, ?)",
                           (entry["customer_id"], idempotency_key, os.getpid(), int(inserted), time.time()))
        if idempotency_key is None:
            row = connection.execute("select * from ledger where row_id = ?", (cursor.lastrowid,)).fetchone()
        else:
            row = connection.execute("select * from ledger where idempotency_key = ?", (idempotency_key,)).fetchone()
    for hook in after_commit_hooks:
        hook(entry["customer_id"])
    return {"row_id": row["row_id"], "total": row["total"], "inserted": inserted}


def start_model_call(path: Path, customer_id: str, attempt: int) -> int:
    with closing(connect(path)) as connection:
        cursor = connection.execute("insert into model_calls (customer_id, attempt, process_id, started_at) values (?, ?, ?, ?)",
                                    (customer_id, attempt, os.getpid(), time.time()))
        return cursor.lastrowid


def finish_model_call(path: Path, call_id: int, cost: float) -> None:
    with closing(connect(path)) as connection:
        connection.execute("update model_calls set finished_at = ?, cost = ? where call_id = ?", (time.time(), cost, call_id))


def start_invocation(path: Path, execution: str, customer_id: str) -> int:
    with closing(connect(path)) as connection:
        cursor = connection.execute(
            "insert into invocations (execution, customer_id, process_id, started_at) values (?, ?, ?, ?)",
            (execution, customer_id, os.getpid(), time.time()))
        return cursor.lastrowid


def finish_invocation(path: Path, invocation_id: int) -> None:
    with closing(connect(path)) as connection:
        connection.execute("update invocations set finished_at = ? where invocation_id = ?", (time.time(), invocation_id))


def queue_for_review(path: Path, customer_id: str, callback_id: str, decision: dict, draft_total: float) -> None:
    """Park a held job for finance. Nothing reaches the ledger until a person answers the callback."""
    with closing(connect(path)) as connection:
        connection.execute(
            "insert or replace into review_queue values (?, ?, ?, ?, ?, ?, 'waiting', null, ?)",
            (customer_id, callback_id, "\n".join(decision["reasons"]), decision["proposed_total"], draft_total,
             decision["adjustment"], time.time()))


def close_review(path: Path, customer_id: str, status: str, decided_by: str) -> None:
    with closing(connect(path)) as connection:
        connection.execute("update review_queue set status = ?, decided_by = ? where customer_id = ?",
                           (status, decided_by, customer_id))


def rows(path: Path, query: str, parameters: tuple = ()) -> list[dict]:
    if not path.exists():
        return []
    with closing(connect(path)) as connection:
        return [dict(row) for row in connection.execute(query, parameters).fetchall()]
