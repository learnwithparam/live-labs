"""`phone serve`: the inbound call wallboard, with live calls, outcomes, and the calls sent to a person."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from northfield.config import OUT, llm_settings
from northfield.schedule import original

PAGE = Path(__file__).with_name("page.html")
DECISIONS = {"dispatch": "Dispatch a technician now", "call_back": "Call back today",
             "refund": "Refund the charge", "closed": "Closed, nothing to do"}


class Desk:
    """The saved review, one background review run at a time, and the office's decision on each handoff."""

    def __init__(self, check_key=llm_settings, runner=None, out: Path = OUT):
        self.check_key, self.runner, self.out = check_key, runner, out
        self.run, self.lock = None, threading.Lock()

    def decisions_file(self) -> Path:
        return self.out / "decisions.json"

    def decisions(self) -> dict:
        return json.loads(self.decisions_file().read_text()) if self.decisions_file().exists() else {}

    def live_calls(self) -> list[dict]:
        """Calls of the current run, read from the status file each agent rewrites as the call goes."""
        if not self.run:
            return []
        calls = []
        for status in sorted((self.out / "calls").glob("*/status.json")):
            if status.stat().st_mtime >= self.run["started"]:
                try:
                    calls.append(json.loads(status.read_text()))
                except (OSError, json.JSONDecodeError):
                    continue  # mid-rename on a busy disk; the next poll reads it
        return calls

    def state(self) -> dict:
        review_file = self.out / "review.json"
        book = original()
        return {"company": book["company"], "line": book["line_number"],
                "review": json.loads(review_file.read_text()) if review_file.exists() else None,
                "decisions": self.decisions(), "choices": DECISIONS, "live": self.live_calls(),
                "run": {key: value for key, value in (self.run or {}).items() if key != "started"} or None}

    def decide(self, call_id: str, decision: str) -> dict:
        with self.lock:
            decisions = self.decisions()
            decisions[call_id] = decision
            self.out.mkdir(exist_ok=True)
            self.decisions_file().write_text(json.dumps(decisions, indent=1))
        return decisions

    def start(self) -> bool:
        if self.run and not self.run["done"]:
            return False
        run = self.run = {"started": time.time(), "done": False, "ringing": []}

        def work():
            try:
                self.check_key()
                runner = self.runner
                if runner is None:
                    from northfield.review import review_calls as runner
                runner(out=self.out, on_call=lambda call_id: run["ringing"].append(call_id))
            # A missing key raises SystemExit in config, which Exception misses; the run would never end.
            except (Exception, SystemExit) as error:  # shown on the page, so a bad key reads as a message, not a hang
                run["error"] = str(error)
            run["done"] = True

        threading.Thread(target=work, daemon=True).start()
        return True


def handler(desk: Desk) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, payload) -> None:
            self.send(200, json.dumps(payload).encode(), "application/json")

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: a foreign page could otherwise spend the key."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            path = urlsplit(self.path).path
            if path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self.send_json(desk.state())
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/api/run":
                if not desk.start():
                    return self.send(409, b"a review is already running", "text/plain")
                return self.send_json({"started": True})
            if self.path == "/api/decide" and body.get("call") and body.get("decision") in DECISIONS:
                return self.send_json(desk.decide(str(body["call"]), body["decision"]))
            self.send(400, b"a decision needs a call and one of the listed choices", "text/plain")

    return Handler


def serve(desk: Desk, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(desk))


def main(port: int) -> int:
    server = serve(Desk(), port)
    print(f"Northfield call wallboard on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
