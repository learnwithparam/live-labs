"""`kettlebay serve`: the release check page, local only, on the runs `kettlebay check` saved."""

import difflib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from kettlebay.assistant import load_golden, load_release
from kettlebay.config import REPEATS
from kettlebay.evals import trajectory_diff
from kettlebay.gate import find_override, load_overrides, needed, record_override, verdict

PAGE = Path(__file__).with_name("page.html")
RELEASES = Path("releases")
RUNS = Path("runs")


def diff_lines(name: str, before: str, after: str) -> list[dict]:
    lines = []
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=1):
        if line.startswith(("---", "+++")):
            continue
        kind = "hunk" if line.startswith("@@") else "add" if line.startswith("+") else "del" if line.startswith("-") else "same"
        lines.append({"kind": kind, "text": line})
    return [{"file": name, "lines": lines}] if lines else []


class Desk:
    """Reads releases, runs and overrides from disk on every request, so a fresh `kettlebay check` shows
    up on a reload without restarting the page."""

    def __init__(self, overrides_file: Path | None = None):
        self.overrides_file = overrides_file
        self.golden = load_golden()

    def override_args(self) -> dict:
        return {"path": self.overrides_file} if self.overrides_file else {}

    def names(self) -> list[str]:
        return sorted((path.stem for path in RELEASES.glob("*.json") if path.stem != "live"),
                      key=lambda name: json.loads((RELEASES / f"{name}.json").read_text()).get("pull_request", 0))

    def run(self, name: str) -> dict | None:
        path = RUNS / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def judged(self, name: str) -> tuple:
        """The release, its run when the run still matches it, and both verdicts."""
        release, live_run, candidate_run = load_release(RELEASES / f"{name}.json"), self.run("live"), self.run(name)
        current = bool(live_run and candidate_run and candidate_run["fingerprint"] == release.fingerprint)
        if not current:
            return release, None, None, None
        return (release, candidate_run, verdict(self.golden, live_run, candidate_run, with_trajectory=True),
                verdict(self.golden, live_run, candidate_run, with_trajectory=False))

    def state(self, release, full) -> str:
        if full is None:
            return "not run"
        if not full.blocked:
            return "passed"
        return "overridden" if not full.broken and find_override(release.fingerprint, **self.override_args()) else "blocked"

    def checks(self) -> dict:
        rows, blocked_full, blocked_answers, judged_count = [], 0, 0, 0
        for name in self.names():
            release, candidate_run, full, answers = self.judged(name)
            if full is not None:
                judged_count += 1
                blocked_full += full.blocked
                blocked_answers += answers.blocked
            rows.append({"name": name, "pull_request": release.spec.get("pull_request"), "title": release.spec["title"],
                         "state": self.state(release, full)})
        return {"checks": rows, "metric": {"judged": judged_count, "blocked": blocked_full, "blocked_answers_only": blocked_answers}}

    def detail(self, name: str) -> dict:
        release, candidate_run, full, answers = self.judged(name)
        live = load_release(RELEASES / "live.json")
        changes = diff_lines(release.spec["prompt"], live.prompt, release.prompt)
        changes += diff_lines(release.spec["tools"], json.dumps(live.tools, indent=1), json.dumps(release.tools, indent=1))
        changes += diff_lines("model", live.model, release.model)
        body = {"name": name, "spec": release.spec, "model": release.model, "fingerprint": release.fingerprint,
                "changes": changes, "state": self.state(release, full), "repeats": REPEATS,
                "needed": needed(REPEATS), "overrides": [entry for entry in load_overrides(**self.override_args())
                                                          if entry["release"] == name]}
        if full is None:
            return {**body, "cases": [], "summary": None}
        live_run = self.run("live")
        cases = []
        for case, row, answers_row in zip(self.golden, full.rows, answers.rows):
            conversations = candidate_run["cases"].get(case["id"], [])
            runs = [{"answer": conversation["answer"], "error": conversation.get("error"), **score,
                     "diff": trajectory_diff(case["trajectory"], conversation["calls"])}
                    for conversation, score in zip(conversations, row["candidate"]["scores"])]
            cases.append({"id": case["id"], "request": case["request"], "signed_off": case["signed_off"],
                          "expected": case["trajectory"], "status": row["status"], "answers_only_status": answers_row["status"],
                          "live_passes": row["live"]["passes"], "passes": row["candidate"]["passes"],
                          "answer_passes": answers_row["candidate"]["passes"], "runs": runs})
        return {**body, "cases": cases, "cost": candidate_run["cost"], "live_cost": live_run["cost"],
                "seconds": candidate_run["seconds"], "made_at": candidate_run["made_at"],
                "summary": {"blocked": full.blocked, "regressed": full.regressed, "unsafe": full.unsafe,
                            "broken": full.broken, "answers_only_blocked": answers.blocked}}

    def override(self, name: str, by: str, reason: str) -> dict:
        release, _, full, _ = self.judged(name)
        if full is None:
            raise ValueError("This release has no current run. Run kettlebay check first.")
        record_override(full, release.fingerprint, by, reason, **self.override_args())
        return self.detail(name)


def handler(desk: Desk) -> type[BaseHTTPRequestHandler]:
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
            """Only this machine's own page may call: an override ships a release."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            path = urlsplit(self.path).path
            if path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/checks":
                return self.send_json(200, desk.checks())
            name = path.removeprefix("/api/check/")
            if path.startswith("/api/check/") and name in desk.names():
                return self.send_json(200, desk.detail(name))
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            parts = urlsplit(self.path).path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "check"] or parts[2] not in desk.names() or parts[3] != "override":
                return self.send(404, b"not found", "text/plain")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                return self.send_json(200, desk.override(parts[2], str(body.get("by", "")), str(body.get("reason", ""))))
            except (ValueError, json.JSONDecodeError) as error:
                return self.send_json(400, {"error": str(error)})

    return Handler


def serve(desk: Desk, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(desk))


def main(port: int) -> int:
    server = serve(Desk(), port)
    print(f"Kettlebay release checks on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
