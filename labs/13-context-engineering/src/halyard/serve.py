"""`audit serve`: Halyard's audit run console, where accounts payable watches an audit's bill grow step by step."""

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from halyard.agent import audit
from halyard.books import billed, invoices
from halyard.context import BREAKS, POLICIES, with_breaks
from halyard.review import disputes, score

PAGE = Path(__file__).with_name("page.html")
REVIEW = Path("out/review.json")


class Console:
    """Runs audits on background threads; the page polls each run for its steps as they land."""

    def __init__(self, model, review_file: Path = REVIEW):
        # Built on first use, so the page opens before any model client exists.
        self.make_model, self.model_cache = model, None
        self.review_file, self.runs, self.lock = review_file, {}, threading.Lock()

    def model(self):
        with self.lock:
            self.model_cache = self.model_cache or self.make_model()
            return self.model_cache

    def overview(self) -> dict:
        review = json.loads(self.review_file.read_text()) if self.review_file.exists() else None
        return {"invoices": [{"id": invoice["id"], "number": invoice["number"], "carrier": invoice["carrier"],
                              "destination": invoice["destination"], "total": billed(invoice)} for invoice in invoices()],
                "breaks": list(BREAKS), "review": review}

    def start(self, policy: str, breaks: list[str]) -> str:
        run = {"policy": policy, "breaks": breaks, "steps": [], "done": False}
        run_id = uuid.uuid4().hex[:8]
        self.runs[run_id] = run

        def go():
            try:
                client, model_name = self.model()
                result = audit(with_breaks(POLICIES[policy], breaks), client, model_name, run["steps"].append)
                run.update(model=model_name, score=score(result), disputes=disputes(result), summary=result["summary"])
            # A missing key raises SystemExit, which `Exception` misses; the run would hang on the page.
            except (Exception, SystemExit) as error:
                run.update(error=str(error))
            run["done"] = True

        threading.Thread(target=go, daemon=True).start()
        return run_id


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
            """Only this machine's own page may call: a foreign page could otherwise spend the key."""
            hosts = {f"{host_name}:{self.server.server_port}" for host_name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            if self.path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/overview":
                return self.send(200, json.dumps(console.overview()).encode(), "application/json")
            if self.path.startswith("/api/run/") and self.path[9:] in console.runs:
                return self.send(200, json.dumps(console.runs[self.path[9:]]).encode(), "application/json")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/api/run":
                breaks = data.get("breaks") or []
                if data.get("context") not in POLICIES or not set(breaks) <= set(BREAKS):
                    return self.send(400, b"a context of naive or engineered", "text/plain")
                return self.send(200, json.dumps({"id": console.start(data["context"], breaks)}).encode(), "application/json")
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(console: Console, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(console))


def main(port: int) -> int:
    from halyard.cli import model

    server = serve(Console(model), port)
    print(f"Halyard audit run console on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
