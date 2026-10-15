"""`support serve`: Kestrel Outdoor's help page, with the customer's orders live from the order system."""

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from kestrel.budget import MAX_COST, MAX_STEPS, Budget
from kestrel.chats import chats, matches
from kestrel.review import outcome, run_chat
from kestrel.shop import Shop

PAGE = Path(__file__).with_name("page.html")
REVIEW = Path("out/review.json")


class Desk:
    """Runs chats on background threads; the page polls each run for its steps and the orders."""

    def __init__(self, models, review_file: Path = REVIEW):
        # Built on first use, so the page opens before any model client exists.
        self.make_models, self.models_cache = models, None
        self.review_file, self.runs, self.lock = review_file, {}, threading.Lock()

    def models(self) -> dict:
        with self.lock:
            self.models_cache = self.models_cache or self.make_models()
            return self.models_cache

    def chats(self) -> dict:
        shop = Shop()
        review = json.loads(self.review_file.read_text()) if self.review_file.exists() else None
        return {"chats": [{"id": c.id, "customer": c.customer, "name": shop.customer_name(c.customer), "message": c.message,
                           "handoff": c.handoff, "orders": shop.orders_of(c.customer)} for c in chats()],
                "review": {e: r["score"] for e, r in review.items()} if review else None,
                "budget": {"max_steps": MAX_STEPS, "max_cost": MAX_COST}}

    def start(self, chat_id: str, engine: str, message: str | None) -> str:
        seeded = next(c for c in chats() if c.id == chat_id)
        text = (message or "").strip() or seeded.message
        run = {"steps": [], "done": False, "chat": seeded.id, "engine": engine, "orders": Shop().orders_of(seeded.customer),
               # A message the learner typed has no answer key, so it is shown but not scored.
               "scored": text == seeded.message}
        rid = uuid.uuid4().hex[:8]
        self.runs[rid] = run

        def go():
            try:
                shop, budget, got = run_chat(engine, seeded.customer, text, self.models(), run["steps"].append,
                                             Budget(self.models()["model"]))
                run.update(reply=got["reply"], stopped=got["stopped"], changes=shop.changes(), handoffs=shop.handoffs(),
                           orders=shop.orders_of(seeded.customer), budget=budget.state(),
                           outcome=outcome(seeded, shop, got) if run["scored"] else None,
                           expect=seeded.expect, matches=matches(seeded, shop))
            except Exception as e:  # shown on the page, so a bad key reads as a message, not a hang
                run.update(error=str(e))
            run["done"] = True

        threading.Thread(target=go, daemon=True).start()
        return rid


def handler(desk: Desk) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: a foreign page could otherwise cancel orders."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            if self.path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/chats":
                return self.send(200, json.dumps(desk.chats()).encode(), "application/json")
            if self.path.startswith("/api/run/") and self.path[9:] in desk.runs:
                return self.send(200, json.dumps(desk.runs[self.path[9:]]).encode(), "application/json")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/api/run":
                if data.get("chat") not in {c.id for c in chats()} or data.get("engine") not in ("reply", "loop", "adk"):
                    return self.send(400, b"a seeded chat and an engine", "text/plain")
                rid = desk.start(data["chat"], data["engine"], data.get("message"))
                return self.send(200, json.dumps({"id": rid}).encode(), "application/json")
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(desk: Desk, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(desk))


def main(port: int) -> int:
    from kestrel.cli import models

    server = serve(Desk(models), port)
    print(f"Kestrel help page on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
