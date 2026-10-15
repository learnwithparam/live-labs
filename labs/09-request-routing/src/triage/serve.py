"""`triage serve`: Tallyroll's triage board, with the inbox landing in queues as each router decides."""

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from triage.review import WAYS, review
from triage.route import CONFIDENT, destination, second_opinion
from triage.tickets import Ticket, inbox
from triage.tickets import queues as queues_guide

PAGE = Path(__file__).with_name("page.html")
REVIEW = Path("out/review.json")
HANDOFFS = Path("out/handoffs.jsonl")


class Room:
    """The routers, this morning's inbox, the last review, and the tickets a person has routed."""

    def __init__(self, routers: dict, review_file: Path = REVIEW, handoffs: Path = HANDOFFS):
        self.routers = {how: routers[how] for how in ("embedding", "llm", "decision") if how in routers}
        self.review_file, self.handoffs_file = review_file, handoffs
        self.tickets = inbox()
        # A review from `triage review` is reused, so the board opens on real numbers without 200 model calls.
        self.report = json.loads(review_file.read_text()) if review_file.exists() else None
        self.lock = threading.Lock()

    def route(self, text: str) -> dict:
        ticket = Ticket("new", "", text, "")
        got = {how: router(ticket) for how, router in self.routers.items()}
        if "llm" in got and "decision" in got:
            got["both"] = second_opinion(got["llm"], got["decision"])
        return {how: {"queue": route.queue, "confidence": round(route.confidence, 3), "to": destination(route),
                      "probabilities": route.probabilities, "ms": round(route.ms), "cost": route.cost} for how, route in got.items()}

    def run(self):
        """Yield each routed ticket as it lands, then the summaries, and keep the result for the next visitor."""
        landed: queue.Queue = queue.Queue()
        done = object()

        def work():
            try:
                landed.put(review(self.tickets, self.routers, on_row=landed.put))
            finally:
                landed.put(done)

        # One review at a time: the decision model on a laptop answers one ticket at a time anyway.
        with self.lock:
            threading.Thread(target=work, daemon=True).start()
            while (event := landed.get()) is not done:
                if "how" in event:
                    yield {"row": event}
                    continue
                self.report = event
                self.review_file.parent.mkdir(parents=True, exist_ok=True)
                self.review_file.write_text(json.dumps(event, indent=1))
                yield {"summary": {how: event[how] for how in WAYS if how in event}}

    def hand_off(self, ticket_id: str, queue: str) -> list[dict]:
        self.handoffs_file.parent.mkdir(parents=True, exist_ok=True)
        with self.handoffs_file.open("a") as out:
            out.write(json.dumps({"id": ticket_id, "queue": queue}) + "\n")
        return self.handoffs()

    def handoffs(self) -> list[dict]:
        if not self.handoffs_file.exists():
            return []
        return [json.loads(line) for line in self.handoffs_file.read_text().splitlines() if line.strip()]


def handler(room: Room) -> type[BaseHTTPRequestHandler]:
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

        def body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            if self.path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/room":
                return self.send_json({
                    "queues": queues_guide(), "confident": CONFIDENT, "ways": [way for way in WAYS if way == "both" or way in room.routers],
                    "tickets": [{"id": ticket.id, "subject": ticket.subject, "body": ticket.body, "solved_in": ticket.queue} for ticket in room.tickets],
                    "review": room.report, "handoffs": room.handoffs()})
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            data = self.body()
            if self.path == "/api/route":
                text = str(data.get("text", "")).strip()
                return self.send_json(room.route(text)) if text else self.send(400, b"a ticket", "text/plain")
            if self.path == "/api/review":
                # One JSON line per routed ticket, flushed as it lands: the board fills while the routers work.
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for event in room.run():
                    self.wfile.write((json.dumps(event) + "\n").encode())
                    self.wfile.flush()
                return None
            if self.path == "/api/handoff":
                queue = str(data.get("queue", ""))
                if queue not in queues_guide() or not data.get("id"):
                    return self.send(400, b"a ticket id and one of the six queues", "text/plain")
                return self.send_json(room.hand_off(str(data["id"]), queue))
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(room: Room, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(room))
