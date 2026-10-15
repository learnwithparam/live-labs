"""`wiki serve`: Ledgerline's runbook search, with the search quality report beside it."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from wiki.ingest import Passage
from wiki.score import Label, evaluate, rank_of
from wiki.search import WAYS, Search

PAGE = Path(__file__).with_name("page.html")
DECISIONS = ("label", "runbook")


def shown(passage: Passage) -> dict:
    return {"id": passage.id, "doc": passage.doc, "section": passage.section, "page": passage.page, "text": passage.text}


class Room:
    """The index, the labelled questions, the quality report once measured, and the person's decisions."""

    def __init__(self, search: Search, golden: list[Label], reviews: Path):
        self.search, self.golden, self.reviews = search, golden, reviews
        self.report: dict | None = None
        # One search at a time: the models and the in-memory index are shared by every request.
        self.lock = threading.Lock()

    def find(self, question: str, how: str) -> dict:
        with self.lock:
            hits = self.search.find(question, how)
            label = next((label for label in self.golden if label.question == question), None)
            ranks = {way: rank_of(self.search, label, way) for way in WAYS} if label else None
        return {
            "how": how,
            "hits": [shown(hit.passage) | {"score": round(hit.score, 3)} for hit in hits],
            "label": label.passage if label else None,
            # The labelled section in full, so a person can read it beside what the search returned.
            "labelled": next((shown(passage) for passage in self.search.passages if label and passage.id == label.passage), None),
            "ranks": ranks,
        }

    def quality(self) -> dict:
        with self.lock:
            if self.report is None:
                self.report = evaluate(self.search, self.golden)
            return self.report

    def decide(self, question: str, decision: str, passage: str | None) -> list[dict]:
        self.reviews.parent.mkdir(parents=True, exist_ok=True)
        with self.reviews.open("a") as out:
            out.write(json.dumps({"question": question, "decision": decision, "passage": passage}) + "\n")
        return self.decisions()

    def decisions(self) -> list[dict]:
        if not self.reviews.exists():
            return []
        return [json.loads(line) for line in self.reviews.read_text().splitlines() if line.strip()]


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
            """Only this machine's own page may call: a rebound Host or a foreign Origin could write decisions."""
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
                docs = sorted({passage.doc for passage in room.search.passages})
                return self.send_json({"docs": docs, "sections": len(room.search.passages), "ways": WAYS,
                                       "questions": [label.question for label in room.golden], "decisions": room.decisions()})
            if self.path == "/api/quality":
                return self.send_json(room.quality())
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            data = self.body()
            question = str(data.get("question", "")).strip()
            if self.path == "/api/search":
                how = data.get("how")
                if how not in WAYS or not question:
                    return self.send(400, b"a way and a question", "text/plain")
                return self.send_json(room.find(question, how))
            if self.path == "/api/review":
                if data.get("decision") not in DECISIONS or not question:
                    return self.send(400, b"a question and a decision", "text/plain")
                return self.send_json(room.decide(question, data["decision"], data.get("passage")))
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(room: Room, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(room))
