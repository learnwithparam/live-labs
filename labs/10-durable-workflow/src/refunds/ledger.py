"""The payments provider's ledger: every payout it made, which is the truth this lab is scored on.

It stands in for a card processor's refunds API, such as Stripe's, and behaves like one in the way that
matters here: a request that carries an idempotency key it has seen before returns the first payout
instead of paying again. A request without a key is always a new payout.
"""

import sqlite3
import time
from contextlib import closing
from pathlib import Path

DEFAULT = Path("out/ledger.db")

SCHEMA = """
create table if not exists payouts (
  id integer primary key, refund text not null, customer text not null, cents integer not null,
  how text not null, idempotency_key text unique, at real not null);
create table if not exists model_calls (refund text not null, how text not null, at real not null);
create table if not exists approvals (refund text primary key, verdict text not null, manager text not null);
"""


class Ledger:
    def __init__(self, path: Path = DEFAULT):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        # A worker and the page write at the same time; wait for the lock instead of failing.
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def pay(self, refund: str, customer: str, cents: int, how: str, key: str | None) -> dict:
        """Pay out once per idempotency key. The second call with the same key returns the first payout."""
        with closing(self.connect()) as db, db:
            if key:
                first = db.execute("select * from payouts where idempotency_key = ?", (key,)).fetchone()
                if first:
                    return dict(first) | {"replayed": True}
            cursor = db.execute("insert into payouts (refund, customer, cents, how, idempotency_key, at) values (?, ?, ?, ?, ?, ?)",
                                (refund, customer, cents, how, key, time.time()))
            return dict(db.execute("select * from payouts where id = ?", (cursor.lastrowid,)).fetchone()) | {"replayed": False}

    def payouts(self, how: str | None = None) -> list[dict]:
        with closing(self.connect()) as db:
            rows = db.execute("select * from payouts where ? is null or how = ? order by id", (how, how)).fetchall()
        return [dict(row) for row in rows]

    def count_call(self, refund: str, how: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute("insert into model_calls values (?, ?, ?)", (refund, how, time.time()))

    def calls(self, how: str) -> dict[str, int]:
        with closing(self.connect()) as db:
            rows = db.execute("select refund, count(*) from model_calls where how = ? group by refund", (how,)).fetchall()
        return {row[0]: row[1] for row in rows}

    # The plain script has no workflow to signal, so a manager's verdict waits in a table it polls.
    def record_verdict(self, refund: str, verdict: str, manager: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute("insert or replace into approvals values (?, ?, ?)", (refund, verdict, manager))

    def verdict(self, refund: str) -> str | None:
        with closing(self.connect()) as db:
            row = db.execute("select verdict from approvals where refund = ?", (refund,)).fetchone()
        return row[0] if row else None

    def clear(self) -> None:
        with closing(self.connect()) as db, db:
            db.executescript("delete from payouts; delete from model_calls; delete from approvals;")
