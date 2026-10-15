"""`claims serve`: the claim photo estimator an adjuster works from, with live runs and decisions."""

import asyncio
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from saltmarsh.assess import assess
from saltmarsh.claims import claims, photo_path, rates, truth
from saltmarsh.triage import REASONS

PAGE = Path(__file__).with_name("page.html")
OUT = Path("out")
DECISIONS = ("approve", "new_photos", "total_loss", "decline", "counter_fraud")
PHOTO = re.compile(r"^/photos/(P\d\d)\.jpg$")


class Desk:
    """The saved run, one live run on a background thread, and the adjuster's decision on each claim."""

    def __init__(self, models, out: Path = OUT):
        self.make_models, self.models_cache, self.out = models, None, out
        self.run: dict = {"running": False}
        self.lock = threading.Lock()

    def models(self) -> tuple:
        with self.lock:
            self.models_cache = self.models_cache or self.make_models()
            return self.models_cache

    def saved(self, name: str):
        path = self.out / name
        return json.loads(path.read_text()) if path.exists() else None

    def state(self) -> dict:
        adjuster = truth()
        bills = {claim_id: row.get("bill") for claim_id, row in adjuster["claims"].items()}
        return {"claims": claims(), "bills": bills, "reasons": REASONS, "currency": rates()["currency"],
                "run": self.saved("run.json"), "naive": self.saved("naive.json"),
                "decisions": self.saved("decisions.json") or {}, "status": self.run}

    def decide(self, claim_id: str, decision: str) -> dict:
        with self.lock:
            decisions = self.saved("decisions.json") or {}
            decisions[claim_id] = decision
            self.out.mkdir(exist_ok=True)
            (self.out / "decisions.json").write_text(json.dumps(decisions, indent=1))
        return decisions

    def start(self) -> None:
        self.run = {"running": True}

        def work():
            try:
                client, model_name = self.models()
                run = asyncio.run(assess(client, model_name))
                self.run = {"running": False, "score": run["score"]}
            # A missing key raises SystemExit in config, which Exception misses; the run would never end.
            except (Exception, SystemExit) as error:
                self.run = {"running": False, "error": str(error)}

        threading.Thread(target=work, daemon=True).start()


def handler(desk: Desk) -> type[BaseHTTPRequestHandler]:
    known_claims = {claim["id"] for claim in claims()}

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
            photo = PHOTO.match(path)
            if photo and photo_path(photo.group(1)).exists():
                return self.send(200, photo_path(photo.group(1)).read_bytes(), "image/jpeg")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/api/run":
                if desk.run["running"]:
                    return self.send(409, b"a run is already going", "text/plain")
                desk.start()
                return self.send_json(desk.run)
            if self.path == "/api/decide" and body.get("claim") in known_claims and body.get("decision") in DECISIONS:
                return self.send_json(desk.decide(body["claim"], body["decision"]))
            self.send(400, b"a decision needs a known claim and one of " + ", ".join(DECISIONS).encode(), "text/plain")

    return Handler


def serve(desk: Desk, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(desk))


def main(port: int) -> int:
    from saltmarsh.config import model_client

    server = serve(Desk(model_client), port)
    print(f"Saltmarsh claim photo estimator on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
