"""`brickhaven-rfc serve`: the RFC review page, local only, reading the RFC files on every request."""

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from brickhaven_rfc.benchmark import load_signoff
from brickhaven_rfc.config import PORT, RFC
from brickhaven_rfc.review import review, send_back
from brickhaven_rfc.rfc import read_rfc

PAGE = Path(__file__).with_name("page.html")
FILES = ("draft.md", "rfc.toml")


def verdict_payload(name: str) -> dict:
    rfc = read_rfc(RFC / name)
    verdict = review(rfc)
    signoff = load_signoff()
    return {"file": name, "title": rfc.title, "approved": verdict.approved, "steps": verdict.steps, "total_ms": verdict.total_ms,
            "signed_ms": verdict.signed_ms, "plan_gaps": verdict.plan_gaps, "eval_plan": rfc.eval_plan, "changed": verdict.changed,
            "cases_by_tag": verdict.cases_by_tag, "signed_by": signoff["signed_by"], "signed_on": signoff["signed_on"],
            "modes": [asdict(mode) | {"measured": not mode.problems} for mode in verdict.modes]}


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def json_reply(self, status: int, payload: dict) -> None:
        self.reply(status, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/":
            self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/review":
            name = parse_qs(url.query).get("file", [FILES[0]])[0]
            self.json_reply(200, verdict_payload(name)) if name in FILES else self.json_reply(404, {"error": "no such RFC"})
        else:
            self.reply(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        # A page on another site must not be able to send a failure mode back: only this page may.
        origin = self.headers.get("Origin", "")
        if origin and urlsplit(origin).hostname not in ("127.0.0.1", "localhost"):
            return self.json_reply(403, {"error": "local only"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path != "/api/send-back" or body.get("file") not in FILES:
            return self.json_reply(404, {"error": "not found"})
        if not str(body.get("reviewer", "")).strip() or len(str(body.get("reason", "")).split()) < 4:
            return self.json_reply(400, {"error": "Give your name and a reason of at least 4 words."})
        send_back(read_rfc(RFC / body["file"]), body["mode"], body["reviewer"], body["reason"])
        self.json_reply(200, verdict_payload(body["file"]))

    def log_message(self, *_: object) -> None:
        pass


def serve(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def run() -> None:
    server = serve(PORT)
    print(f"RFC review on http://127.0.0.1:{PORT}")
    server.serve_forever()
