"""`research serve`: the research report reader, with a live subagent tree and the editor's decisions."""

import asyncio
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from kitebird.corpus import load_brief, load_sources
from kitebird.lead import run_lead, run_single

PAGE = Path(__file__).with_name("page.html")
OUT = Path("out")
RUNNERS = {"single": run_single, "lead": run_lead}
DECISIONS = ("keep", "cut")


class Desk:
    """Saved reports, live runs on background threads, and the editor's keep or cut on each flagged claim."""

    def __init__(self, models, out: Path = OUT):
        self.make_models, self.models_cache, self.out = models, None, out
        self.runs, self.lock = {}, threading.Lock()

    def models(self) -> tuple:
        with self.lock:
            self.models_cache = self.models_cache or self.make_models()
            return self.models_cache

    def edits_file(self) -> Path:
        return self.out / "edits.json"

    def edits(self) -> dict:
        return json.loads(self.edits_file().read_text()) if self.edits_file().exists() else {}

    def state(self) -> dict:
        reports = {version: json.loads((self.out / f"{version}.json").read_text())
                   for version in RUNNERS if (self.out / f"{version}.json").exists()}
        sources = {source_id: {"title": source.title, "publisher": source.publisher, "date": source.date}
                   for source_id, source in load_sources().items()}
        return {"brief": load_brief(), "reports": reports, "edits": self.edits(), "sources": sources,
                "running": next((run_id for run_id, run in self.runs.items() if not run["done"]), None)}

    def edit(self, report_id: str, claim_id: str, decision: str) -> dict:
        """Decisions are filed under the report's id, so a fresh run never inherits them by claim number."""
        with self.lock:
            edits = self.edits()
            edits.setdefault(report_id, {})[claim_id] = decision
            self.out.mkdir(exist_ok=True)
            self.edits_file().write_text(json.dumps(edits, indent=1))
        return edits

    def start(self, version: str) -> str:
        run_id = uuid.uuid4().hex[:8]
        run = {"version": version, "agents": {}, "order": [], "done": False}
        self.runs[run_id] = run

        def on_event(agent_state: dict) -> None:
            # A copy, so the page never reads a dict the agent loop is still changing.
            snapshot = json.loads(json.dumps(agent_state))
            if snapshot["agent"] not in run["agents"]:
                run["order"].append(snapshot["agent"])
            run["agents"][snapshot["agent"]] = snapshot

        def work():
            try:
                client, model_name = self.models()
                report = asyncio.run(RUNNERS[version](client, model_name, on_event))
                self.out.mkdir(exist_ok=True)
                (self.out / f"{version}.json").write_text(json.dumps(report, indent=1))
                run["score"] = report["score"]
            # A missing key raises SystemExit in config, which Exception misses; the run would never end.
            except (Exception, SystemExit) as error:  # shown on the page, so a bad key reads as a message, not a hang
                run["error"] = str(error)
            run["done"] = True

        threading.Thread(target=work, daemon=True).start()
        return run_id

    def view(self, run_id: str) -> dict:
        run = self.runs[run_id]
        return {**run, "agents": [run["agents"][name] for name in list(run["order"])]}


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
            source = load_sources().get(path.removeprefix("/api/source/"))
            if path.startswith("/api/source/") and source:
                return self.send_json(vars(source))
            run_id = path.removeprefix("/api/run/")
            if path.startswith("/api/run/") and run_id in desk.runs:
                return self.send_json(desk.view(run_id))
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/api/run" and body.get("version") in RUNNERS:
                if desk.state()["running"]:
                    return self.send(409, b"a run is already going", "text/plain")
                return self.send_json({"id": desk.start(body["version"])})
            if self.path == "/api/edit" and body.get("report") and body.get("claim") and body.get("decision") in DECISIONS:
                return self.send_json(desk.edit(str(body["report"]), str(body["claim"]), body["decision"]))
            self.send(400, b"a run needs a version; an edit needs a report, a claim and keep or cut", "text/plain")

    return Handler


def serve(desk: Desk, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(desk))


def main(port: int) -> int:
    from kitebird.config import model_client

    server = serve(Desk(model_client), port)
    print(f"Kitebird research reader on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
