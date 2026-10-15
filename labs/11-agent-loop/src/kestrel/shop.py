"""Kestrel Outdoor's order system: the source of truth the agent changes and the review reads.

Each chat gets a fresh copy in memory, so every engine starts from the same orders. The review never
reads what the agent said. It reads what changed here.
"""

import json
import sqlite3
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"


class Shop:
    def __init__(self, path: Path = DATA / "orders.json"):
        seed = json.loads(path.read_text())
        # The shop's clock is pinned, so the 14 and 30 day windows give the same answer on every run.
        self.today = date.fromisoformat(seed["today"])
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            create table orders (id text primary key, customer text, name text, item text, total real,
                                 status text, address text, delivered text, shipment text);
            create table changes (seq integer primary key, kind text, order_id text, amount real, detail text);
            create table handoffs (seq integer primary key, reason text);
        """)
        for o in seed["orders"]:
            self.db.execute("insert into orders values (?,?,?,?,?,?,?,?,?)",
                            (o["id"], o["customer"], o["name"], o["item"], o["total"], o["status"], o["address"],
                             o.get("delivered"), json.dumps(o.get("shipment"))))

    def order(self, order_id: str) -> dict | None:
        row = self.db.execute("select * from orders where id = ?", (order_id,)).fetchone()
        return {**dict(row), "shipment": json.loads(row["shipment"])} if row else None

    def orders_of(self, customer: str) -> list[dict]:
        ids = [r["id"] for r in self.db.execute("select id from orders where customer = ? order by id desc", (customer,))]
        return [self.order(i) for i in ids]

    def customer_name(self, customer: str) -> str:
        return self.db.execute("select name from orders where customer = ?", (customer,)).fetchone()["name"]

    def change(self, kind: str, order_id: str, status: str | None = None, amount: float | None = None, detail: str = "") -> None:
        # One row per change, so the review compares what happened, not just where the order ended up.
        if status:
            self.db.execute("update orders set status = ? where id = ?", (status, order_id))
        if kind == "address":
            self.db.execute("update orders set address = ? where id = ?", (detail, order_id))
        self.db.execute("insert into changes (kind, order_id, amount, detail) values (?,?,?,?)", (kind, order_id, amount, detail))

    def hand_off(self, reason: str) -> None:
        self.db.execute("insert into handoffs (reason) values (?)", (reason,))

    def changes(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("select kind, order_id, amount, detail from changes order by seq")]

    def handoffs(self) -> list[str]:
        return [r["reason"] for r in self.db.execute("select reason from handoffs order by seq")]
