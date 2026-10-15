"""`receipts serve`: one page that shows each receipt beside the card charge it pays for, and why."""

import json
import mimetypes
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from receipts.extract import Attempt, naive, read_receipt
from receipts.match import Charge, settle
from receipts.schema import LineItem, Receipt

PAGE = Path(__file__).with_name("page.html")


def arithmetic(reply: str) -> dict | None:
    """The reconcile sum behind one reply, passed or not, from the same `expected_total` the rule uses."""
    try:
        data = json.loads(reply)
        items = [LineItem.model_construct(**item) for item in data.get("line_items", [])]
        receipt = Receipt.model_construct(**{**data, "line_items": items})
        return {
            "items": [[item.description, item.amount] for item in items],
            "discount": receipt.discount,
            "tax": receipt.tax,
            "tax_included": receipt.tax_included,
            "tip": receipt.tip,
            "expected": receipt.expected_total(),
            "total": receipt.total,
        }
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None


def attempts(history: list[Attempt]) -> list[dict]:
    return [{"reply": attempt.reply, "error": attempt.error, "sum": arithmetic(attempt.reply)} for attempt in history]


def charge_row(charge: Charge) -> dict:
    return {**asdict(charge), "date": charge.date.isoformat()}


class Desk:
    """The receipts folder, the card feed, the model and what has been matched so far."""

    def __init__(self, folder: Path, charges: list[Charge], client, model: str):
        from receipts.cli import receipts_in

        self.files = {path.name: path for path in receipts_in(folder)}
        self.charges, self.client, self.model = charges, client, model
        self.results: dict[str, dict] = {}

    def extract(self, name: str) -> dict:
        self.results.pop(name, None)
        taken = {result["charge"]["id"] for result in self.results.values() if result["charge"]}
        settled = settle(read_receipt(self.files[name]), self.client, self.model, self.charges, taken)
        done = settled.extraction or settled.failed
        found = settled.match
        outcome = {
            "status": "matched" if found.charge else "person",
            "entry": json.loads(settled.extraction.receipt.model_dump_json()) if settled.extraction else None,
            "attempts": attempts(done.history),
            "charge": charge_row(found.charge) if found.charge else None,
            "checks": found.checks,
            "reason": found.reason,
        }
        self.results[name] = outcome
        return outcome

    def naive(self, name: str) -> dict:
        reply = naive(read_receipt(self.files[name]), self.client, self.model)
        try:
            json.loads(reply)
            plain = True
        except json.JSONDecodeError:
            plain = False
        return {"reply": reply, "plain_json": plain}


def handler(desk: Desk) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, data: object) -> None:
            self.send(200, json.dumps(data).encode(), "application/json")

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: a rebound Host or a foreign Origin could spend the key."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            if self.path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/receipts":
                names = [{"name": name, "kind": path.suffix[1:]} for name, path in desk.files.items()]
                charges = [charge_row(charge) for charge in desk.charges]
                return self.send_json({"model": desk.model, "receipts": names, "charges": charges, "results": desk.results})
            name = self.path.removeprefix("/files/")
            if name in desk.files:
                kind = mimetypes.guess_type(name)[0] or "text/plain"
                return self.send(200, desk.files[name].read_bytes(), kind)
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            action, _, name = self.path.removeprefix("/api/").partition("/")
            if name not in desk.files or action not in ("extract", "naive"):
                return self.send(404, b"not found", "text/plain")
            self.send_json(desk.extract(name) if action == "extract" else desk.naive(name))

    return Handler


def serve(desk: Desk, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(desk))
