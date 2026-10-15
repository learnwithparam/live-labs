"""`listing serve`: a Brightdoor property page with the buyer chat, and the fair housing reviewer's queue."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from brightdoor.assistant import listings, messages
from brightdoor.review import NAMES, handle

PAGE = Path(__file__).with_name("page.html")
REVIEW = Path("out/review.json")
DECISIONS = Path("out/decisions.json")
CHOICES = ("send the draft", "keep the rewrite")


class Desk:
    """Answers buyer messages one at a time and keeps the reviewer's queue and decisions."""

    def __init__(self, models, review_file: Path = REVIEW, decisions_file: Path = DECISIONS):
        self.make_models, self.models_cache = models, None
        self.review_file, self.decisions_file = review_file, decisions_file
        self.live: list[dict] = []
        self.lock = threading.Lock()

    def models(self) -> tuple:
        with self.lock:
            self.models_cache = self.models_cache or self.make_models()
            return self.models_cache

    def review(self) -> dict | None:
        return json.loads(self.review_file.read_text()) if self.review_file.exists() else None

    def decisions(self) -> dict:
        return json.loads(self.decisions_file.read_text()) if self.decisions_file.exists() else {}

    def queue(self) -> list[dict]:
        review = self.review()
        rows = [row for row in (review["rails"]["rows"] if review else []) if row["decision"] == "review"]
        decided = self.decisions()
        return [{**row, "human": decided.get(row["id"])} for row in self.live + rows]

    def state(self) -> dict:
        review = self.review()
        rows = review["rails"]["rows"] if review else []
        return {"listings": listings(), "messages": messages(), "names": NAMES, "choices": CHOICES,
                "score": {arm: result["score"] for arm, result in review.items()} if review else None,
                "planted": {row["listing"]: row["quote"] for row in rows if row["quote"]},
                "queue": self.queue()}

    def ask(self, listing_id: str, text: str, arm: str) -> dict:
        client, model, guard, _ = self.models()
        item = {"id": f"C-{len(self.live) + 1:02d}", "listing": listing_id, "text": text,
                "label": "unlabelled", "kind": "live", "why": "Typed on the property page."}
        row = asyncio.run(handle(item, listings()[listing_id], client, model, guard if arm == "rails" else None))
        if row["decision"] == "review":
            self.live.insert(0, row)
        return row

    def decide(self, row_id: str, choice: str) -> dict:
        decided = self.decisions()
        decided[row_id] = {"choice": choice, "at": time.strftime("%Y-%m-%d %H:%M")}
        self.decisions_file.parent.mkdir(exist_ok=True)
        self.decisions_file.write_text(json.dumps(decided, indent=1))
        return decided[row_id]


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
            path = urlsplit(self.path).path
            if path == "/api/ask":
                text = str(body.get("message") or "").strip()
                if body.get("listing") not in listings() or not text or body.get("arm") not in NAMES:
                    return self.send(400, b"a listing, a message and an arm", "text/plain")
                try:
                    return self.send_json(desk.ask(body["listing"], text[:600], body["arm"]))
                # A missing key raises SystemExit, which `Exception` misses; the page would wait forever.
                except (Exception, SystemExit) as error:
                    return self.send_json({"error": str(error)})
            if path == "/api/decide":
                if body.get("choice") not in CHOICES or not str(body.get("id") or "").strip():
                    return self.send(400, b"an id and a choice", "text/plain")
                return self.send_json(desk.decide(str(body["id"]), body["choice"]))
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(desk: Desk, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(desk))


def main(port: int) -> int:
    from brightdoor.cli import models

    server = serve(Desk(models), port)
    print(f"Brightdoor listing page on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
