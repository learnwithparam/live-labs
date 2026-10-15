"""`nightly serve`: Meterline's nightly run console, local only. It brings the local runner back up on the
last run's checkpoints so finance can answer the held jobs from the page."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from meterline.batch import build_report
from meterline.config import HOST_PORT, OUT, PERIOD, RUNNER_PORT, UNATTENDED_LIMIT
from meterline.review import answer
from meterline.runtime import LocalCloud

PAGE = Path(__file__).with_name("page.html")


class Console:
    """The page's data: the last run's report, rebuilt from the runner on request, and finance's answers."""

    def __init__(self, cloud: LocalCloud):
        self.cloud, self.lock = cloud, threading.Lock()
        self.has_run = (cloud.out / "run.json").exists()

    def state(self) -> dict:
        if not self.has_run:
            return {"report": None}
        with self.lock:
            report = build_report(self.cloud)
        naive_file = self.cloud.out / "naive.json"
        return {"report": report, "naive": json.loads(naive_file.read_text()) if naive_file.exists() else None,
                "period": PERIOD, "limit": UNATTENDED_LIMIT}

    def decide(self, customer_id: str, approved: bool, reviewer: str) -> dict:
        with self.lock:
            return answer(self.cloud, customer_id, approved, reviewer)


def handler(console: Console) -> type[BaseHTTPRequestHandler]:
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
            """Only this machine's own page may call: the ledger holds customers' invoices."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            path = urlsplit(self.path).path
            if path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/run":
                return self.send(200, json.dumps(console.state()).encode(), "application/json")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if urlsplit(self.path).path != "/api/review" or body.get("decision") not in ("approve", "reject"):
                return self.send(404, b"not found", "text/plain")
            reviewer = str(body.get("reviewer") or "").strip()[:60]
            if not reviewer:
                return self.send(400, json.dumps({"error": "Say who is deciding."}).encode(), "application/json")
            try:
                lane = console.decide(str(body.get("customer_id")), body["decision"] == "approve", reviewer)
            except ValueError as error:
                return self.send(409, json.dumps({"error": str(error)}).encode(), "application/json")
            self.send(200, json.dumps(lane).encode(), "application/json")

    return Handler


def start(cloud: LocalCloud, port: int) -> tuple[ThreadingHTTPServer, Console]:
    console = Console(cloud)
    if console.has_run:
        cloud.start_runner()
        cloud.start_host()
    return ThreadingHTTPServer(("127.0.0.1", port), handler(console)), console


def serve(port: int) -> None:
    cloud = LocalCloud(OUT, RUNNER_PORT, HOST_PORT)
    server, console = start(cloud, port)
    print(f"Meterline nightly run console on http://127.0.0.1:{port}. Ctrl-C stops it."
          + ("" if console.has_run else " No run yet: run `uv run nightly run` first."))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cloud.stop()
