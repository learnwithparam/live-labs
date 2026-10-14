"""`policy serve`: Ledgerline's on-call policy desk, with the agent's steps streamed as they happen."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from policy.grade import Question, grade
from policy.graph import build, start
from policy.plain import plain
from policy.review import review
from policy.search import Policies

PAGE = Path(__file__).with_name("page.html")
REVIEW = Path("out/review.json")


class Room:
    """The policies, the expert's questions, the last review, and what was sent to the policy owners."""

    def __init__(self, policies: Policies, questions: list[Question], client, model: str, judge: str, escalated: Path):
        self.policies, self.questions = policies, questions
        self.client, self.model, self.judge, self.escalated_file = client, model, judge, escalated
        self.graph = build(policies, client, model)
        # A review from `policy review` is reused, so the page opens on real numbers without 100 model calls.
        self.report = json.loads(REVIEW.read_text()) if REVIEW.exists() else None
        self.lock = threading.Lock()

    def sections(self, ids: list[str]) -> list[dict]:
        return [{"id": p.id, "doc": p.doc, "section": p.section, "page": p.page, "text": p.text} for p in self.policies.get(ids)]

    def run(self, question: str, how: str):
        """Yield each step as it finishes, then the answer with its sections."""
        began = time.monotonic()
        if how == "plain":
            result = plain(question, self.policies, self.client, self.model)
            yield {"node": "search", "searches": result["searches"], "at": time.monotonic() - began}
        else:
            result, cost, calls = start(question), 0.0, 0
            for step in self.graph.stream(result, stream_mode="updates"):
                for node, update in step.items():
                    # An update holds only what that node added; the totals are summed here, as the graph's reducers do.
                    cost, calls = cost + update.get("cost", 0.0), calls + update.get("calls", 0)
                    result |= update | {"cost": cost, "calls": calls}
                    if node != "answer":
                        yield {"node": node, **update, "at": time.monotonic() - began}
        yield {"node": "answer", "answer": result["answer"], "cited": result["cited"], "covered": result["covered"],
               "sources": self.sections(result["cited"] or result["sources"]), "cost": result["cost"],
               "calls": result["calls"], "at": time.monotonic() - began}

    def check(self, qid: str, answer: str) -> dict | None:
        q = next((q for q in self.questions if q.id == qid), None)
        if q is None:
            return None
        graded, _ = grade(q, answer, self.client, self.judge)
        return graded | {"points": q.points}

    def quality(self) -> dict:
        # One review at a time, run once: it is about 100 model calls, and every viewer sees the same result.
        with self.lock:
            if self.report is None:
                self.report = review(self.questions, self.policies, self.client, self.model, self.judge)
                REVIEW.parent.mkdir(parents=True, exist_ok=True)
                REVIEW.write_text(json.dumps(self.report, indent=1))
            return self.report

    def escalate(self, question: str, answer: str) -> list[dict]:
        self.escalated_file.parent.mkdir(parents=True, exist_ok=True)
        with self.escalated_file.open("a") as out:
            out.write(json.dumps({"question": question, "answer": answer}) + "\n")
        return self.escalated()

    def escalated(self) -> list[dict]:
        if not self.escalated_file.exists():
            return []
        return [json.loads(line) for line in self.escalated_file.read_text().splitlines() if line.strip()]


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
                return self.send_json({"questions": [{"id": q.id, "question": q.question} for q in room.questions],
                                       "sections": len(room.policies.passages), "model": room.model, "judge_model": room.judge,
                                       "escalated": room.escalated(), "reviewed": room.report is not None})
            if self.path == "/api/review":
                return self.send_json(room.quality() | {"points": {q.id: q.points for q in room.questions}})
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            data = self.body()
            if self.path == "/api/ask":
                question = str(data.get("question", "")).strip()
                if not question:
                    return self.send(400, b"a question", "text/plain")
                # One JSON line per step, flushed as it happens: the page draws the trace while the agent works.
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for event in room.run(question, "plain" if data.get("how") == "plain" else "agentic"):
                    self.wfile.write((json.dumps(event) + "\n").encode())
                    self.wfile.flush()
                return None
            if self.path == "/api/grade":
                got = room.check(str(data.get("id", "")), str(data.get("answer", "")))
                return self.send_json(got) if got else self.send(404, b"no such question", "text/plain")
            if self.path == "/api/escalate":
                if not data.get("question"):
                    return self.send(400, b"a question", "text/plain")
                return self.send_json(room.escalate(str(data["question"]), str(data.get("answer", ""))))
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(room: Room, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(room))
