"""`quill serve`: Quillmark finance's month-end AI cost close, on this machine only."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

PAGE = Path(__file__).with_name("page.html")


class Books:
    """What the page reads and writes: the saved report, and finance's decision on one unattributed line."""

    def __init__(self, load=None, decide=None):
        from quillmark import report

        self.load = load or (lambda: json.loads(report.REPORT.read_text()) if report.REPORT.exists() else None)
        self.decide = decide or report.record_decision


def handler(books: Books) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status: int, value) -> None:
            self.send(status, json.dumps(value).encode(), "application/json")

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: a foreign page could otherwise read the company's costs."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            path = urlsplit(self.path).path
            if path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/report":
                built = books.load()
                if built is None:
                    return self.send_json(404, {"error": "No report yet. Run quill month, then quill report."})
                return self.send_json(200, built)
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            if self.path != "/api/decide":
                return self.send(404, b"not found", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            built = books.load() or {"attribution": {"unattributed": []}, "customers": {}, "features": []}
            open_lines = {line["generation_id"] for line in built["attribution"]["unattributed"]}
            if body.get("generation_id") not in open_lines:
                return self.send_json(400, {"error": "That line is not in the unattributed bucket."})
            if body.get("customer") in built["customers"] and body.get("feature") in built["features"]:
                decision = {"customer": str(body["customer"]), "feature": str(body["feature"])}
            elif body.get("flag"):
                decision = {"flag": True}
            else:
                return self.send_json(400, {"error": "Assign a customer and a feature, or flag the line."})
            self.send_json(200, books.decide(body["generation_id"], decision))

    return Handler


def serve(books: Books, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(books))


def main(port: int) -> int:
    server = serve(Books(), port)
    print(f"Quillmark AI cost close on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
