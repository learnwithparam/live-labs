"""The orchestrator: issues in, pull requests out, several sessions at once, nobody at a keyboard.

For each issue it starts a fresh sandbox from `main`, runs the agent in it, checks what the sandbox could
see, terminates it, pushes the change, runs CI in a second fresh sandbox and opens a pull request with
the diff, the CI result, the cost and the session log. Every way a session can go wrong ends in a status
with a reason, never in a pull request that hides it.
"""

import json
import secrets
import threading
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fernhill.ci import run_ci
from fernhill.config import (
    ACCEPTANCE,
    BASE_IMAGE,
    NETWORK,
    PARALLEL_SESSIONS,
    PLANTED_SECRET,
    PREFIX,
    PRICES,
    PROXY_HOST_PORT,
    PROXY_NAME,
    SESSION_DOLLARS,
    SESSION_SECONDS,
)
from fernhill.forge import Forge
from fernhill.sandbox import AGENT_COMMAND, DockerSandbox, Sandbox, docker, wait_for

# Run inside each sandbox after the agent stops: everything a leaked secret could hide in.
EXPOSURE_PROBE = ("env; tr '\\0' '\\n' < /proc/1/environ; cat ~/.config/fernhill/credentials 2>&1; "
                  "ls -a / /tmp 2>&1")


@dataclass
class Session:
    """One issue's trip through a sandbox. The page polls these while they run."""

    issue: dict
    status: str = "queued"  # queued, working, ci, pull request, gave up, failed
    reason: str = ""
    log: list[dict] = field(default_factory=list)
    started: float | None = None
    ended: float | None = None
    cost: float = 0.0
    pull: int | None = None
    sandbox: str = ""
    secret_seen: bool = False
    exposure: str = ""

    def seconds(self) -> float:
        if self.started is None:
            return 0.0
        return round((self.ended or time.monotonic()) - self.started, 1)

    def public(self) -> dict:
        return {**{key: value for key, value in asdict(self).items() if key not in ("started", "ended")},
                "seconds": self.seconds()}


class Egress:
    """The host's handle on the egress proxy container: start it, open a session budget, read the ledger."""

    def __init__(self, admin_token: str | None = None, host_port: int = PROXY_HOST_PORT):
        self.admin_token = admin_token or secrets.token_urlsafe(24)
        self.admin_url = f"http://127.0.0.1:{host_port}/admin"
        self.inside_url = f"http://{PROXY_NAME}:8321/v1"
        self.host_port = host_port

    def start(self, image: str, upstream: str, key: str, model: str) -> None:
        import os

        docker("rm", "--force", PROXY_NAME, check=False)
        # The real key enters the proxy's environment and no other container's.
        secret_env = {"UPSTREAM_KEY": key, "ADMIN_TOKEN": self.admin_token}
        docker("run", "--detach", "--name", PROXY_NAME, "--label", "lab32=egress", "--read-only",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "256m",
               "--publish", f"127.0.0.1:{self.host_port}:8321", "--env", "UPSTREAM_KEY", "--env", "ADMIN_TOKEN",
               "--env", f"UPSTREAM_URL={upstream}", "--env", f"MODEL={model}",
               "--env", "MODEL_PRICES=" + ",".join(str(price) for price in PRICES[model]),
               image, "python", "/opt/fleet/egress.py",
               env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], **secret_env})
        docker("network", "connect", NETWORK, PROXY_NAME)
        if not wait_for(lambda: self.ledger() is not None, 20):
            raise RuntimeError("the egress proxy did not come up")

    def call(self, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.admin_url}/{path}", data=data,
                                         headers={"X-Admin-Token": self.admin_token, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def open_session(self, token: str, session: str, budget: float) -> None:
        self.call("sessions", {"token": token, "session": session, "budget": budget})

    def ledger(self) -> dict | None:
        try:
            return self.call("ledger")
        except OSError:
            return None


def docker_sandboxes(image: str) -> tuple[Callable, Callable]:
    """Two factories over one image: agent sandboxes join the egress network, CI sandboxes get no network."""

    def for_agent(workspace: Path, name: str, env: dict) -> Sandbox:
        return DockerSandbox.create(image, workspace, f"{PREFIX}{name}", env, NETWORK)

    def for_ci(workspace: Path, name: str) -> Sandbox:
        return DockerSandbox.create(image, workspace, f"{PREFIX}{name}", {}, None)

    return for_agent, for_ci


class Fleet:
    """Runs a queue of issues through sandboxes, `parallel` at a time, under per-session budgets."""

    def __init__(self, forge: Forge, egress, agent_sandbox: Callable, ci_sandbox: Callable, model: str, out: Path,
                 parallel: int = PARALLEL_SESSIONS, max_seconds: float = SESSION_SECONDS,
                 max_dollars: float = SESSION_DOLLARS):
        self.forge, self.egress, self.agent_sandbox, self.ci_sandbox = forge, egress, agent_sandbox, ci_sandbox
        self.model, self.out, self.parallel = model, out, parallel
        self.max_seconds, self.max_dollars = max_seconds, max_dollars
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()

    def run(self, issues: list[dict]) -> list[Session]:
        for issue in issues:
            self.sessions[issue["id"]] = Session(issue)
        with ThreadPoolExecutor(max_workers=self.parallel) as pool:
            list(pool.map(self.run_issue, issues))
        return [self.sessions[issue["id"]] for issue in issues]

    def record(self, session: Session, line: str) -> None:
        """One line of the agent's output. A line that is not an event still lands in the log, as text."""
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = {"type": "text", "text": line}
        with self.lock:
            session.log.append(event)

    def run_issue(self, issue: dict) -> Session:
        session = self.sessions.setdefault(issue["id"], Session(issue))
        session.started, session.status = time.monotonic(), "working"
        name = issue["id"].lower()
        workspace = self.out / "sessions" / name / "workspace"
        try:
            base = self.forge.export("main", workspace)
            token = secrets.token_urlsafe(24)
            self.egress.open_session(token, issue["id"], self.max_dollars)
            env = {"LLM_BASE_URL": self.egress.inside_url, "LLM_API_KEY": token, "LLM_MODEL": self.model}
            sandbox = self.agent_sandbox(workspace, name, env)
            session.sandbox = sandbox.name
            try:
                ran = sandbox.exec([*AGENT_COMMAND, json.dumps(issue)], timeout=self.max_seconds,
                                   on_line=lambda line: self.record(session, line))
                if not ran.timed_out:
                    session.exposure = sandbox.exec(["sh", "-c", EXPOSURE_PROBE], timeout=30).output
            finally:
                sandbox.terminate()
            self.settle(session, ran, workspace, base)
        except Exception as error:  # noqa: BLE001 - a broken session is reported, never allowed to stop the queue
            session.status, session.reason = "failed", f"orchestrator error: {error}"
        session.ended = session.ended or time.monotonic()
        return session

    def settle(self, session: Session, ran, workspace: Path, base: str) -> None:
        """Turn what the sandbox did into a status. Budgets are checked before anything is pushed."""
        issue = session.issue
        spent = (self.egress.ledger() or {}).get(issue["id"], {})
        session.cost = round(spent.get("spent", 0.0), 6)
        outcome = next((event for event in reversed(session.log) if event.get("type") == "outcome"), {})
        visible = json.dumps(session.log) + session.exposure
        session.secret_seen = PLANTED_SECRET in visible
        if ran.timed_out:
            session.status, session.reason = "failed", f"wall time budget of {self.max_seconds:.0f}s exceeded; sandbox terminated"
        elif any("cost budget" in refusal for refusal in spent.get("refused", [])):
            session.status, session.reason = "failed", f"cost budget of ${self.max_dollars:g} spent; sandbox terminated"
        elif outcome.get("status") == "gave_up":
            session.status, session.reason = "gave up", outcome.get("reason", "")
        elif outcome.get("status") != "finished":
            session.status, session.reason = "failed", outcome.get("reason") or f"agent exited with code {ran.exit_code}"
        if session.status != "working":
            session.ended = time.monotonic()
            return
        pushed = self.forge.push_branch(workspace, f"agent/{issue['id'].lower()}", base,
                                        f"{issue['id']}: {issue['title']}")
        if pushed is None:
            session.status, session.reason, session.ended = "failed", "the agent finished with no change", time.monotonic()
            return
        session.status = "ci"
        ci = run_ci(self.forge, f"agent/{issue['id'].lower()}", self.out / "ci" / issue["id"].lower(), self.ci_sandbox,
                    ACCEPTANCE / f"test_{issue['id'].lower().replace('-', '_')}.py")
        session.ended = time.monotonic()
        record = self.forge.open_pull({
            "issue": {key: issue[key] for key in ("id", "title", "reporter", "body")}, "branch": f"agent/{issue['id'].lower()}",
            "title": f"{issue['id']}: {issue['title']}", "description": outcome.get("summary", ""), "base": base,
            **pushed, "ci": ci, "cost": session.cost, "seconds": session.seconds(), "model": self.model,
            "session_log": session.log, "secret_seen": session.secret_seen,
            "attention": attention(pushed, ci)})
        session.pull, session.status = record["number"], "pull request"


def attention(pushed: dict, ci: dict) -> list[str]:
    """What a reviewer should look at first. These never block a merge; red CI does."""
    reasons = []
    if ci["status"] != "passed":
        failed = next(step["name"] for step in ci["steps"] if not step["passed"])
        reasons.append(f"CI failed at {failed}: it cannot be merged. Close it, or run the issue again.")
    edited_tests = [file["path"] for file in pushed["files"] if file["path"].startswith("tests/") and file["removed"]]
    if edited_tests:
        reasons.append("changes existing tests: " + ", ".join(edited_tests))
    if any(path.startswith("tests/") for path in pushed["deleted"]):
        reasons.append("deletes a test file")
    if any(file["path"].startswith(".github/") for file in pushed["files"]):
        reasons.append("edits the CI workflow (CI still ran main's)")
    return reasons


def scoreboard(sessions: list[dict], pulls: list[dict], wall_seconds: float) -> dict:
    """The numbers the team watches. The headline is the share of agent pull requests a person merged."""
    opened = len(pulls)
    green = sum(pull["ci"]["status"] == "passed" for pull in pulls)
    merged = sum(pull["state"] == "merged" for pull in pulls)
    cost = sum(session["cost"] for session in sessions)
    return {"issues": len(sessions), "pulls": opened, "ci_green": green, "merged": merged,
            "closed": sum(pull["state"] == "closed" for pull in pulls),
            "waiting": sum(pull["state"] == "open" for pull in pulls),
            "merged_share": round(merged / opened, 3) if opened else 0.0,
            "ci_pass_rate": round(green / opened, 3) if opened else 0.0,
            "cost": round(cost, 6), "cost_per_pull": round(cost / opened, 6) if opened else None,
            "wall_seconds": round(wall_seconds, 1),
            "gave_up": [session["issue"]["id"] for session in sessions if session["status"] == "gave up"],
            "failed": [session["issue"]["id"] for session in sessions if session["status"] == "failed"],
            "secret_seen": sum(session["secret_seen"] for session in sessions), "base_image": BASE_IMAGE}
