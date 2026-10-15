"""`refunds serve`: Fennick's refund risk review queue, read from the last back-test, with the reviewer's
calls filed to out/reviews.json. Local only: a foreign page must never pay or hold a refund."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from fennick.decide import REVIEW_CHOICES

PAGE = Path(__file__).with_name("page.html")


class Queue:
    def __init__(self, out: Path = Path("out")):
        self.out = out

    def read(self, name: str):
        path = self.out / name
        return json.loads(path.read_text()) if path.exists() else None

    def state(self) -> dict:
        llm = self.read("llm.json")
        if llm:
            llm.pop("scores", None)  # a thousand scores the page never draws
        return {"backtest": self.read("backtest.json"), "llm": llm, "reviews": self.read("reviews.json") or {},
                "choices": REVIEW_CHOICES}

    def decide(self, refund_id: str, decision: str) -> dict:
        reviews = self.read("reviews.json") or {}
        reviews[refund_id] = decision
        self.out.mkdir(exist_ok=True)
        (self.out / "reviews.json").write_text(json.dumps(reviews, indent=1))
        return reviews


def handler(queue: Queue) -> type[BaseHTTPRequestHandler]:
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
                return self.send(200, json.dumps(queue.state()).encode(), "application/json")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            if self.path != "/api/decide":
                return self.send(404, b"not found", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            backtest = queue.read("backtest.json") or {"queue": []}
            known = {row["refund_id"] for row in backtest["queue"]}
            if body.get("refund") not in known or body.get("decision") not in REVIEW_CHOICES:
                return self.send(400, b"a refund from the back-test and a decision: pay or hold", "text/plain")
            reviews = queue.decide(body["refund"], body["decision"])
            self.send(200, json.dumps(reviews).encode(), "application/json")

    return Handler


def serve(queue: Queue, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(queue))


def main(port: int) -> int:
    server = serve(Queue(), port)
    print(f"Fennick refund review queue on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
