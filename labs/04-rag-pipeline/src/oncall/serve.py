"""`oncall serve`: one page that answers the on-call questions both ways and shows which answers a person takes."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from oncall.desk import Desk
from oncall.score import load_questions

PAGE = Path(__file__).with_name("page.html")
WAYS = ("size", "section")


class Room:
    """The docs, the questions, the model, and each way's results once run."""

    def __init__(self, docs: Path, questions: Path, client, model: str):
        self.docs, self.questions = docs, load_questions(questions)
        self.client, self.model = client, model
        self.runs: dict[str, dict] = {}

    def run(self, how: str) -> dict:
        if how not in self.runs:
            self.runs[how] = Desk(self.docs, self.client, self.model, how).run(self.questions)
        return self.runs[how]


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

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            if self.path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/room":
                docs = sorted(p.name for p in room.docs.glob("*.pdf"))
                return self.send_json({"model": room.model, "docs": docs, "questions": [q.question for q in room.questions]})
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            how = self.path.removeprefix("/api/answer/")
            if how not in WAYS:
                return self.send(404, b"not found", "text/plain")
            self.send_json(room.run(how))

    return Handler


def serve(room: Room, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(room))
