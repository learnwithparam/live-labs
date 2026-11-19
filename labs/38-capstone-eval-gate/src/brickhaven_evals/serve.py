"""`brickhaven-evals serve`: the labelling bench and release scorecard, local only."""

import datetime
import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import brickhaven_evals.config as settings
from brickhaven_evals.extractor import load_run
from brickhaven_evals.golden import load_golden, verify_case, waiting_for_a_person
from brickhaven_evals.scoring import gate

PAGE = Path(__file__).with_name("page.html")
CANDIDATES = ("bad", "harmless")


def bench_payload() -> dict:
    golden = load_golden()
    return {"verified": len(golden), "tags": sorted({case["tag"] for case in golden}),
            "waiting": waiting_for_a_person(), "thresholds": settings.THRESHOLDS}


def scorecard_payload(name: str) -> dict:
    """Score only cases the saved runs saw: a case verified since the last run has no output yet."""
    try:
        live_run, candidate_run = load_run("live", settings.RUNS), load_run(name, settings.RUNS)
    except FileNotFoundError:
        return {"ran": False, "candidate": name}
    golden = load_golden()
    seen = [case for case in golden if case["id"] in live_run and case["id"] in candidate_run]
    verdict = gate(seen, live_run, candidate_run)
    return {"ran": True, "candidate": name, "cases": len(seen), "unscored": len(golden) - len(seen),
            "verdict": asdict(verdict) | {"ships": verdict.ships, "naive_ships": verdict.naive_ships}}


class Handler(BaseHTTPRequestHandler):
    def json_reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/":
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/bench":
            self.json_reply(200, bench_payload())
        elif url.path == "/api/scorecard":
            name = parse_qs(url.query).get("candidate", [CANDIDATES[0]])[0]
            self.json_reply(200, scorecard_payload(name)) if name in CANDIDATES else self.json_reply(404, {"error": "no such candidate"})
        else:
            self.json_reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        # A page on another site must not be able to verify a case: only this page may.
        origin = self.headers.get("Origin", "")
        if origin and urlsplit(origin).hostname not in ("127.0.0.1", "localhost"):
            return self.json_reply(403, {"error": "local only"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path != "/api/label":
            return self.json_reply(404, {"error": "not found"})
        if not str(body.get("labeller", "")).strip():
            return self.json_reply(400, {"error": "Give your name: a case counts only when a person verified it."})
        try:
            verify_case(body.get("case_id", ""), body["labeller"], datetime.date.today().isoformat(), body.get("corrected"))
        except KeyError:
            return self.json_reply(404, {"error": "no such proposed case"})
        self.json_reply(200, bench_payload())

    def log_message(self, *_: object) -> None:
        pass


def serve(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def run() -> None:
    server = serve(settings.PORT)
    print(f"Labelling bench on http://127.0.0.1:{settings.PORT}")
    server.serve_forever()
