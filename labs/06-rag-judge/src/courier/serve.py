"""`support serve`: Swiftbite's courier support inbox, with the judge's agreement report beside it."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from courier.agree import Labelled
from courier.desk import handle, review
from courier.guard import guard
from courier.kb import HelpCentre

PAGE = Path(__file__).with_name("page.html")
DECISIONS = ("send", "rewrite")


class Room:
    """The help centre, the labelled replies, live checks once run, and the agent's decisions."""

    def __init__(self, help_centre: HelpCentre, labelled: list[Labelled], questions: list[str], client, model: str, judge: str, decisions: Path):
        self.help_centre, self.labelled, self.questions = help_centre, labelled, questions
        self.client, self.model, self.judge, self.decisions_file = client, model, judge, decisions
        self.checked: dict[str, dict] = {}
        self.report: dict | None = None
        self.lock = threading.Lock()

    def tickets(self) -> list[dict]:
        return [{"id": r.id, "courier": r.courier, "question": r.question, "answer": r.answer, "cited": r.cited} for r in self.labelled]

    def sources(self, ids: list[str]) -> list[dict]:
        return [{"id": p.id, "article": p.article, "section": p.section, "text": p.text} for p in self.help_centre.get(ids)]

    def check(self, ticket: str) -> dict | None:
        row = next((r for r in self.labelled if r.id == ticket), None)
        if row is None:
            return None
        if ticket not in self.checked:
            got = guard(row.question, row.answer, self.help_centre.get(row.cited), self.client, self.model)
            self.checked[ticket] = {"send": got.send, "claims": got.claims, "why": got.why}
        return self.checked[ticket] | {"sources": self.sources(row.cited)}

    def ask(self, question: str) -> dict:
        got = handle(question, self.help_centre, self.client, self.model)
        return got | {"sources": self.sources(got["cited"])}

    def quality(self) -> dict:
        # One review at a time, run once: it is 60 model calls, and every viewer sees the same result.
        with self.lock:
            if self.report is None:
                self.report = review(self.labelled, self.help_centre, self.client, self.model, self.judge)
            return self.report | {"model": self.model, "judge_model": self.judge}

    def decide(self, ticket: str, decision: str) -> list[dict]:
        self.decisions_file.parent.mkdir(parents=True, exist_ok=True)
        with self.decisions_file.open("a") as out:
            out.write(json.dumps({"ticket": ticket, "decision": decision}) + "\n")
        return self.decisions()

    def decisions(self) -> list[dict]:
        if not self.decisions_file.exists():
            return []
        return [json.loads(line) for line in self.decisions_file.read_text().splitlines() if line.strip()]


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
                return self.send_json({"tickets": room.tickets(), "questions": room.questions, "decisions": room.decisions(),
                                       "sections": len(room.help_centre.passages), "model": room.model, "judge_model": room.judge})
            if self.path == "/api/quality":
                return self.send_json(room.quality())
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            data = self.body()
            if self.path == "/api/check":
                got = room.check(str(data.get("ticket", "")))
                return self.send_json(got) if got else self.send(404, b"no such ticket", "text/plain")
            if self.path == "/api/ask":
                question = str(data.get("question", "")).strip()
                if not question:
                    return self.send(400, b"a question", "text/plain")
                return self.send_json(room.ask(question))
            if self.path == "/api/decide":
                if data.get("decision") not in DECISIONS or not data.get("ticket"):
                    return self.send(400, b"a ticket and a decision", "text/plain")
                return self.send_json(room.decide(str(data["ticket"]), data["decision"]))
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(room: Room, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(room))
