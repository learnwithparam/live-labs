"""`agent serve`: Wrenfield's coding agent session, with approval cards a person answers in the page."""

import json
import threading
import time
import uuid
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from wrenfield.config import PAGE_PORT, PDP_GRPC_PORT, PDP_HTTP_PORT
from wrenfield.policy import Policy, PromptOnly, check_table, table
from wrenfield.review import MODES, WORK, people, tasks
from wrenfield.tools import TOOLS

PAGE = Path(__file__).with_name("page.html")
REVIEW = Path("out/review.json")
AUDIT = Path("out/audit.jsonl")
WAIT_FOR_PERSON = 600  # seconds an approval card waits before it counts as a no


class Session:
    """Runs requests on background threads. A run that needs a person parks until the page answers."""

    def __init__(self, models, cerbos, review_file: Path = REVIEW, audit_file: Path = AUDIT):
        self.make_models, self.models_cache, self.lock = models, None, threading.Lock()
        self.policies = {"policy": Policy(cerbos), "prompt": PromptOnly(Policy(cerbos))}
        self.review_file, self.audit_file, self.runs = review_file, audit_file, {}
        self.cells = check_table(self.policies["policy"])

    def models(self) -> tuple:
        with self.lock:
            self.models_cache = self.models_cache or self.make_models()
            return self.models_cache

    def audit(self) -> list[dict]:
        if not self.audit_file.exists():
            return []
        return [json.loads(line) for line in self.audit_file.read_text().splitlines()[-60:]]

    def state(self) -> dict:
        review = json.loads(self.review_file.read_text()) if self.review_file.exists() else None
        return {"people": list(people().values()), "tasks": tasks(), "modes": MODES, "table": table(), "cells": self.cells,
                "tools": [{"name": spec["name"], **spec["annotations"]} for spec in TOOLS], "audit": self.audit(),
                "review": review}

    def wait(self, run: dict, pending: dict):
        """Park the agent's thread until the page posts an answer, or until the wait runs out."""
        answered = threading.Event()
        run.update(pending={**pending, "id": uuid.uuid4().hex[:8]}, answered=answered, answer=None)
        answered.wait(WAIT_FOR_PERSON)
        answer, run["pending"] = run["answer"], None
        return answer

    def start(self, mode: str, person_id: str, message: str, task_id: str | None) -> str:
        person = people()[person_id]
        run = {"steps": [], "done": False, "mode": mode, "person": person, "pending": None, "task": task_id}
        run_id = uuid.uuid4().hex[:8]
        self.runs[run_id] = run

        def record(step: dict) -> None:
            run["steps"].append(step)
            if step["kind"] == "call":
                entry = {"at": time.strftime("%H:%M:%S"), "run": run_id, "mode": mode, "person": person["name"],
                         "role": person["role"], **{key: step[key] for key in ("tool", "verdict", "policy_verdict", "rule",
                                                                                 "reason", "approved_by", "ran", "destructive")},
                         "target": step["args"].get("branch") or step["args"].get("path") or step["args"].get("name")
                         or step["args"].get("environment") or ""}
                with self.lock:
                    self.audit_file.parent.mkdir(exist_ok=True)
                    with self.audit_file.open("a") as handle:
                        handle.write(json.dumps(entry) + "\n")

        def work():
            from wrenfield.agent import forbidden_ran, run as run_agent, unapproved_destructive
            from wrenfield.workspace import Workspace

            try:
                client, model_name = self.models()
                workspace = Workspace.create(WORK / "session" / run_id)
                result = run_agent(message, person, workspace, self.policies[mode], client, model_name,
                                   approve=lambda request_card: bool(self.wait(run, {"kind": "card", **request_card})),
                                   answer=lambda reply: self.wait(run, {"kind": "chat", "reply": reply}) or None,
                                   on_step=record)
                run.update(cost=result["usage"]["cost"], unapproved=unapproved_destructive(result["calls"]),
                           forbidden=forbidden_ran(result["calls"]))
            # A missing key raises SystemExit, which `Exception` misses; the run would hang on the page.
            except (Exception, SystemExit) as error:
                run.update(error=str(error))
            run["done"] = True

        threading.Thread(target=work, daemon=True).start()
        return run_id

    def answer(self, run_id: str, pending_id: str, answer) -> bool:
        run = self.runs[run_id]
        if not run["pending"] or run["pending"]["id"] != pending_id:
            return False
        run["answer"] = answer
        run["answered"].set()
        return True

    def view(self, run_id: str) -> dict:
        run = self.runs[run_id]
        return {key: value for key, value in run.items() if key not in ("answered", "answer")}


def handler(session: Session) -> type[BaseHTTPRequestHandler]:
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
            """Only this machine's own page may call: a foreign page could otherwise approve a force-push."""
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
                return self.send(200, json.dumps(session.state()).encode(), "application/json")
            run_id = path.removeprefix("/api/run/")
            if path.startswith("/api/run/") and run_id in session.runs:
                return self.send(200, json.dumps(session.view(run_id)).encode(), "application/json")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/api/run":
                message = str(body.get("message") or "").strip()
                if body.get("mode") not in MODES or body.get("person") not in people() or not message:
                    return self.send(400, b"a mode, a known person and a message", "text/plain")
                run_id = session.start(body["mode"], body["person"], message, body.get("task"))
                return self.send(200, json.dumps({"id": run_id}).encode(), "application/json")
            run_id = self.path.removeprefix("/api/run/").removesuffix("/answer")
            if self.path.endswith("/answer") and run_id in session.runs:
                if not session.answer(run_id, str(body.get("pending")), body.get("answer")):
                    return self.send(409, b"nothing is waiting for that answer", "text/plain")
                return self.send(200, b"{}", "application/json")
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(session: Session, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(session))


def main(port: int | None) -> int:
    from wrenfield import pdp
    from wrenfield.cli import model

    port = port or PAGE_PORT
    with ExitStack() as stack:
        cerbos = stack.enter_context(pdp.running(PDP_HTTP_PORT, PDP_GRPC_PORT))
        server = serve(Session(model, cerbos), port)
        print(f"Wrenfield agent session on http://127.0.0.1:{port}. Cerbos on port {PDP_HTTP_PORT}. Ctrl-C stops both.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0
