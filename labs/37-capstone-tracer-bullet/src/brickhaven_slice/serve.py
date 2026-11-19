"""`brickhaven-slice serve`: the dispatcher's board, on this machine only, reading the saved boards."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from brickhaven_slice.config import MODES, OUT, PORT

PAGE = Path(__file__).with_name("page.html")


def handler(folder: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may read the board: a foreign page could otherwise read tenant messages."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            return self.headers.get("Host") in hosts and self.headers.get("Origin") in (None, f"http://{self.headers.get('Host')}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            url = urlsplit(self.path)
            if url.path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/board":
                mode = parse_qs(url.query).get("mode", ["slice"])[0]
                path = folder / f"board-{mode}.json"
                if mode not in MODES or not path.exists():
                    message = f"No board for {mode} yet. Run `brickhaven-slice run --mode {mode}`, then `report`."
                    return self.send(404, json.dumps({"error": message}).encode(), "application/json")
                return self.send(200, path.read_bytes(), "application/json")
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(folder: Path = OUT, port: int = PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(folder))


def run() -> None:
    server = serve()
    print(f"Work order board on http://127.0.0.1:{PORT}")
    server.serve_forever()
