"""`gpu serve`: Corvane's GPU endpoint console, local only. It shows the committed GPU run and lets a
reviewer decide the items in the review queue."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from corvane_gpu.cli import current_report
from corvane_gpu.config import DECISIONS, GPU_RUN
from corvane_gpu.review import decide

PAGE = Path(__file__).with_name("page.html")


def handler(run_path: Path, decisions_path: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status: int, payload: dict) -> None:
            self.send(status, json.dumps(payload).encode(), "application/json")

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: the queue holds card transactions."""
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
                return self.send_json(200, current_report(run_path))
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            if urlsplit(self.path).path != "/api/review":
                return self.send(404, b"not found", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            queue_ids = {item["id"] for item in current_report(run_path)["queue"]}
            try:
                decision = decide(decisions_path, queue_ids, str(body.get("id")), str(body.get("category")),
                                  str(body.get("reviewer") or ""))
            except ValueError as error:
                return self.send_json(400, {"error": str(error)})
            self.send_json(200, decision)

    return Handler


def serve(port: int, run_path: Path = GPU_RUN) -> None:
    if not run_path.exists():
        raise SystemExit(f"No GPU run at {run_path}. Run kaggle/run_on_kaggle.sh, or `uv run gpu rehearse` and pass its results.")
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(run_path, DECISIONS))
    print(f"Corvane GPU endpoint console on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
