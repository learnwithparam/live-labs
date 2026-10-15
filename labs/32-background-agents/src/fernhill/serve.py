"""`fleet serve`: Fernhill's pull request queue, local only, with the sandbox sessions live beside it."""

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from fernhill.forge import Forge, MergeRefused
from fernhill.orchestrator import scoreboard

PAGE = Path(__file__).with_name("page.html")
LOG_TAIL = 60


def step_line(event: dict) -> str:
    """One session event as the line the sessions strip shows."""
    if event.get("type") == "tool":
        target = event["arguments"].get("path") or event["arguments"].get("command") or event["arguments"].get("pattern")
        return f"{event['name']} {target}" if target else event["name"]
    if event.get("type") == "text":
        return event.get("text", "")[:120]
    return f"model turn {event.get('turn')}"


def session_card(session: dict) -> dict:
    tools = [event for event in session["log"] if event.get("type") == "tool"]
    return {"id": session["issue"]["id"], "title": session["issue"]["title"], "status": session["status"],
            "reason": session["reason"], "seconds": session["seconds"], "cost": session["cost"],
            "pull": session["pull"], "sandbox": session["sandbox"], "tools": len(tools),
            "last": step_line(tools[-1]) if tools else "", "secret_seen": session["secret_seen"]}


def pull_row(pull: dict) -> dict:
    ci, held_out = pull["ci"], (pull["ci"].get("acceptance") or {})
    failed = next((step["name"] for step in ci["steps"] if not step["passed"]), None)
    return {"number": pull["number"], "id": pull["issue"]["id"], "title": pull["title"],
            "reporter": pull["issue"]["reporter"], "state": pull["state"], "ci": ci["status"], "failed_step": failed,
            "held_out": held_out.get("passed"), "attention": pull["attention"], "cost": pull["cost"],
            "seconds": pull["seconds"], "files": len(pull["files"]), "added": sum(file["added"] for file in pull["files"]),
            "removed": sum(file["removed"] for file in pull["files"]), "reviewer": pull["reviewer"],
            "close_reason": pull.get("close_reason"), "head": pull["head"][:7]}


def pull_detail(pull: dict) -> dict:
    """Everything a reviewer reads before deciding: the issue, the diff, CI's steps and what the agent did."""
    log = [{"turn": event["turn"], "line": step_line(event), "output": event["output"][-600:]}
           for event in pull["session_log"] if event.get("type") == "tool"]
    return {**pull_row(pull), "body": pull["issue"]["body"], "description": pull["description"], "diff": pull["diff"],
            "steps": pull["ci"]["steps"], "workflow_from": pull["ci"]["workflow_from"],
            "acceptance": pull["ci"].get("acceptance"), "log": log[-LOG_TAIL:], "model": pull["model"],
            "base": pull["base"][:7], "file_list": pull["files"]}


class Queue:
    """The page's state: the forge's pull requests, the saved run, and a fleet when one is running."""

    def __init__(self, forge_root: Path, run_file: Path, laptop_file: Path, issues: list[dict],
                 start_fleet: Callable[[Forge], tuple], save_run: Callable):
        self.forge_root, self.run_file, self.laptop_file = forge_root, run_file, laptop_file
        self.issues, self.start_fleet, self.save_run = issues, start_fleet, save_run
        self.lock = threading.Lock()
        self.fleet, self.running, self.error, self.worker = None, False, None, None

    def forge(self) -> Forge:
        return Forge(self.forge_root)  # read fresh each time: the CLI may have merged from another terminal

    def saved(self) -> dict:
        return json.loads(self.run_file.read_text()) if self.run_file.exists() else {"sessions": [], "model": None}

    def state(self) -> dict:
        saved, forge = self.saved(), self.forge()
        sessions = {session["issue"]["id"]: session for session in saved["sessions"]}
        if self.fleet is not None:
            with self.fleet.lock:
                sessions.update({issue_id: live.public() for issue_id, live in self.fleet.sessions.items()})
        ordered = [sessions[issue["id"]] for issue in self.issues if issue["id"] in sessions]
        wall = (saved.get("board") or {}).get("wall_seconds", 0)
        laptop = json.loads(self.laptop_file.read_text()) if self.laptop_file.exists() else None
        return {"running": self.running, "error": self.error, "model": saved.get("model"),
                "sessions": [session_card(session) for session in ordered],
                "pulls": [pull_row(pull) for pull in reversed(forge.pulls)],
                "board": scoreboard(ordered, forge.pulls, wall), "issues": len(self.issues),
                "laptop": laptop and {"probes": {label: result["reached"] for label, result in laptop["probes"].items()},
                                      "blocked_seconds": laptop["blocked_seconds"], "issues": len(laptop["runs"])}}

    def pull(self, number: int) -> dict | None:
        try:
            return pull_detail(self.forge().pull(number))
        except KeyError:
            return None

    def start(self) -> bool:
        """Run the whole queue in the background. False when a run is already going."""
        with self.lock:
            if self.running:
                return False
            self.running, self.error = True, None
        self.worker = threading.Thread(target=self.run_all, daemon=True)
        self.worker.start()
        return True

    def run_all(self) -> None:
        forge, cleanup = self.forge(), None
        try:
            forge.reset()
            fleet, cleanup = self.start_fleet(forge)
            self.fleet = fleet
            started = time.monotonic()
            sessions = fleet.run(self.issues)
            self.save_run(forge, sessions, fleet.model, time.monotonic() - started, self.issues, True)
        # A missing key raises SystemExit; the page shows the message instead of spinning.
        except (Exception, SystemExit) as error:  # noqa: BLE001 - any failure is shown on the page
            self.error = str(error)
        finally:
            if cleanup:
                cleanup()
            self.fleet, self.running = None, False

    def decide(self, number: int, action: str, reviewer: str, reason: str) -> dict:
        forge = self.forge()
        if action == "merge":
            forge.merge(number, reviewer)
        else:
            forge.close(number, reviewer, reason or "closed by the reviewer")
        return pull_detail(forge.pull(number))


def handler(queue: Queue) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status: int, payload) -> None:
            self.send(status, json.dumps(payload).encode())

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: a merge button must not be reachable from another site."""
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
                return self.send_json(200, queue.state())
            number = path.removeprefix("/api/pulls/")
            detail = queue.pull(int(number)) if path.startswith("/api/pulls/") and number.isdigit() else None
            if detail:
                return self.send_json(200, detail)
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            parts = urlsplit(self.path).path.strip("/").split("/")
            if parts == ["api", "run"]:
                return self.send_json(202 if queue.start() else 409, {"running": True})
            if len(parts) != 4 or parts[:2] != ["api", "pulls"] or not parts[2].isdigit() or parts[3] not in ("merge", "close"):
                return self.send(404, b"not found", "text/plain")
            if queue.pull(int(parts[2])) is None:
                return self.send(404, b"no such pull request", "text/plain")
            try:
                detail = queue.decide(int(parts[2]), parts[3], str(body.get("reviewer", "")), str(body.get("reason", "")))
            except MergeRefused as refusal:
                return self.send_json(409, {"refused": str(refusal)})
            self.send_json(200, detail)

    return Handler


def serve(queue: Queue, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(queue))


def main(port: int) -> int:
    from fernhill.cli import LAPTOP_FILE, RUN_FILE, docker_fleet, issues, save_run
    from fernhill.config import OUT, PARALLEL_SESSIONS, SESSION_DOLLARS, SESSION_SECONDS

    def start_fleet(forge: Forge) -> tuple:
        return docker_fleet(forge, PARALLEL_SESSIONS, SESSION_SECONDS, SESSION_DOLLARS)

    server = serve(Queue(OUT / "forge", RUN_FILE, LAPTOP_FILE, issues(), start_fleet, save_run), port)
    print(f"Fernhill pull request queue on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
