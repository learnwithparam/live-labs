"""`driftwell serve`: the release console, local only. It reads Langfuse, the scores and the release log."""

import difflib
import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from driftwell.bot import SupportBot
from driftwell.cli import LIVE_TRAFFIC
from driftwell.config import CACHE_TTL_SECONDS, PASS_BAR
from driftwell.golden import cases
from driftwell.prompt_source import PromptSource
from driftwell.registry import holder, versions
from driftwell.release import (RELEASES, SCORES, ReleaseLog, Refused, Scores, gate, needed, note_reply, promote, rollback,
                              rollback_target)

PAGE = Path(__file__).with_name("page.html")
LANGFUSE_PROMPT_URL = "http://localhost:8270/project/driftwell-support/prompts/support-bot"


class Console:
    """The page's state: the registry, the golden set scores, the release log and one running bot."""

    def __init__(self, make_registry, make_models, scores_path: Path = SCORES, log_path: Path = RELEASES):
        self.make_registry, self.make_models = make_registry, make_models
        self.scores_path, self.log_path = scores_path, log_path
        self.lock, self.client_cache, self.bot_cache = threading.Lock(), None, None
        self.golden = {case["id"]: case for case in cases()}
        self.replies: deque = deque(maxlen=14)
        self.traffic_turn = 0

    def client(self):
        with self.lock:
            self.client_cache = self.client_cache or self.make_registry()
            return self.client_cache

    def bot(self) -> SupportBot:
        # One bot with one prompt cache for the server's life, like the real one: it is never restarted.
        with self.lock:
            if self.bot_cache is None:
                llm, model_name = self.make_models()
                self.bot_cache = SupportBot(PromptSource(self.client_cache), llm, model_name, tracer=self.client_cache)
            return self.bot_cache

    def state(self) -> dict:
        all_versions, scores, log = versions(self.client()), Scores(self.scores_path), ReleaseLog(self.log_path)
        rows = []
        for version in all_versions:
            record = scores.of(version.text)
            rows.append({"number": version.number, "labels": version.labels, "message": version.message,
                         "author": version.author, "blocked": gate(record),
                         "score": None if record is None else {
                             "passed": record["passed"], "total": record["total"], "score": record["score"],
                             "rows": [{key: row[key] for key in ("id", "topic", "message", "reply", "failed", "passed")}
                                      for row in record["rows"]]}})
        production, staging = holder(all_versions, "production"), holder(all_versions, "staging")
        by_number = {row["number"]: row for row in rows}
        waiting = [row for row in rows if "staging" in row["labels"] and row["blocked"]
                   and "production" not in row["labels"]]
        rollbacks = [event for event in log.events if event["kind"] == "rollback" and event["served_after"] is not None]
        return {"versions": rows, "events": log.events, "bar": PASS_BAR, "needed": needed(len(self.golden), PASS_BAR),
                "golden": len(self.golden), "ttl": CACHE_TTL_SECONDS, "langfuse": LANGFUSE_PROMPT_URL,
                "production": production.number if production else None,
                "staging": staging.number if staging else None,
                "previous": rollback_target(log, production.number if production else None),
                "replies": list(self.replies),
                "metric": {"production_score": (by_number[production.number]["score"] if production else None),
                           "waiting": [{"number": row["number"], "reason": row["blocked"], "author": row["author"],
                                        "message": row["message"]} for row in waiting],
                           "last_rollback": rollbacks[-1] if rollbacks else None}}

    def diff(self, older: int, newer: int) -> list[dict]:
        texts = {version.number: version.text.splitlines() for version in versions(self.client())}
        lines = []
        matcher = difflib.SequenceMatcher(a=texts[older], b=texts[newer], autojunk=False)
        for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
            if tag == "equal":
                lines += [{"kind": "same", "text": line} for line in texts[older][old_start:old_end]]
                continue
            lines += [{"kind": "removed", "text": line} for line in texts[older][old_start:old_end]]
            lines += [{"kind": "added", "text": line} for line in texts[newer][new_start:new_end]]
        return lines

    def ask(self, case_id: str | None, message: str | None) -> dict:
        if not case_id and not message:
            case_id = LIVE_TRAFFIC[self.traffic_turn % len(LIVE_TRAFFIC)]
            self.traffic_turn += 1
        case = self.golden.get(case_id or "", {"id": None, "customer": "there", "message": message})
        self.client()
        answered = self.bot().reply(case["message"], case["customer"])
        with self.lock:
            closed = note_reply(ReleaseLog(self.log_path), answered)
        entry = {"case": case["id"], "customer": case["customer"], "message": case["message"],
                 "reply": answered["reply"], "version": answered["version"], "source": answered["source"],
                 "at": answered["at"], "closed_rollback": closed}
        self.replies.appendleft(entry)
        return entry

    def promote(self, number: int, by: str, override: str) -> dict:
        with self.lock:
            return promote(self.client_cache or self.make_registry(), ReleaseLog(self.log_path),
                           Scores(self.scores_path), number, by, override)

    def rollback(self, by: str) -> dict:
        with self.lock:
            return rollback(self.client_cache or self.make_registry(), ReleaseLog(self.log_path), by)


def handler(console: Console) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status: int, payload) -> None:
            self.send(status, json.dumps(payload).encode())

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: a promote here changes what customers read."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            parts = urlsplit(self.path)
            if parts.path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if parts.path == "/api/state":
                return self.send_json(200, console.state())
            if parts.path == "/api/diff":
                query = parse_qs(parts.query)
                try:
                    return self.send_json(200, console.diff(int(query["older"][0]), int(query["newer"][0])))
                except (KeyError, ValueError):
                    return self.send(400, b"older and newer must be version numbers", "text/plain")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            path = urlsplit(self.path).path
            try:
                if path == "/api/ask":
                    return self.send_json(200, console.ask(body.get("case"), body.get("message")))
                if path == "/api/promote":
                    if not isinstance(body.get("version"), int):
                        return self.send(400, b"version must be a number", "text/plain")
                    return self.send_json(200, console.promote(body["version"], str(body.get("by") or ""),
                                                               str(body.get("override") or "")))
                if path == "/api/rollback":
                    return self.send_json(200, console.rollback(str(body.get("by") or "")))
            except Refused as refusal:
                return self.send_json(409, {"error": str(refusal)})
            # A missing key raises SystemExit; the page shows the message instead of hanging.
            except (Exception, SystemExit) as error:
                return self.send_json(502, {"error": str(error)})
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(console: Console, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(console))


def main(port: int) -> int:
    from driftwell.cli import model, registry

    server = serve(Console(registry, model), port)
    print(f"Driftwell release console on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
