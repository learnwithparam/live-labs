"""`postings serve`: one page that scores two prompts against the golden set and shows what the change broke."""

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from postings.extract import read_all
from postings.score import gate, load_golden, right

PAGE = Path(__file__).with_name("page.html")


class Bench:
    """The prompt files, the postings, the approved labels, the model and every run scored so far."""

    def __init__(self, prompts: Path, postings: Path, golden: Path, client, model: str):
        self.prompts = {path.stem: path for path in sorted(prompts.glob("*.txt"))}
        self.golden = load_golden(golden)
        self.postings = {name: (postings / name).read_text() for name in self.golden}
        self.client, self.model = client, model
        self.runs: dict[str, dict] = {}

    def run(self, stem: str) -> dict:
        if stem not in self.runs:
            readings = read_all(self.postings, self.prompts[stem].read_text(), self.client, self.model)
            self.runs[stem] = {"readings": {name: reading.fields for name, reading in readings.items()}, "cost": sum(reading.cost for reading in readings.values())}
        return self.runs[stem]

    def compare(self, before: str, after: str) -> dict:
        verdict = gate(self.golden, self.run(before)["readings"], self.run(after)["readings"])
        return {
            "before": verdict.before,
            "after": verdict.after,
            "total": verdict.total,
            "worse": verdict.worse,
            "blocked": verdict.blocked,
            "needs_person": verdict.needs_person,
            "regressed": [asdict(change) for change in verdict.regressed],
            "fixed": [asdict(change) for change in verdict.fixed],
            # Which filters the new prompt gets right, so the page can tell "wrong with both" from "right with both".
            "right": right(self.golden, self.runs[after]["readings"]),
            "runs": {before: self.runs[before], after: self.runs[after]},
        }


def handler(bench: Bench) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, data: object) -> None:
            self.send(200, json.dumps(data).encode(), "application/json")

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: a rebound Host or a foreign Origin could spend the key."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            if self.path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/bench":
                prompts = {stem: path.read_text() for stem, path in bench.prompts.items()}
                return self.send_json({"model": bench.model, "prompts": prompts, "golden": bench.golden, "postings": bench.postings})
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            parts = self.path.removeprefix("/api/gate/").split("/")
            if len(parts) != 2 or not all(stem in bench.prompts for stem in parts):
                return self.send(404, b"not found", "text/plain")
            self.send_json(bench.compare(*parts))

    return Handler


def serve(bench: Bench, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(bench))
