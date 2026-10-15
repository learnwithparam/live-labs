"""`refund serve`: Fernhill Home's refunds desk, with each workflow's history, the manager's inbox and the ledger."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from temporalio.client import Client

from refunds.history import status, timeline
from refunds.ledger import DEFAULT, Ledger
from refunds.requests import orders, requests
from refunds import naive
from refunds.review import MANAGER, Crash, raise_crash
from refunds.workflow import APPROVAL_DEADLINE, RefundWorkflow, TASK_QUEUE, workflow_id

PAGE = Path(__file__).with_name("page.html")
REVIEW = Path("out/review.json")
# The desk's own queue, so a `refund run` or review worker on the same server never pays its refunds.
DESK_QUEUE = f"{TASK_QUEUE}-desk"


class Room:
    """The desk's state: a Temporal client on the event loop, one worker process, and the payments ledger.

    The HTTP server runs on its own threads, so every Temporal call is handed to the loop and awaited.
    """

    def __init__(self, client: Client, address: str, loop: asyncio.AbstractEventLoop, ledger: Path = DEFAULT, review_file: Path = REVIEW,
                 llm=None):
        self.client, self.address, self.loop = client, address, loop
        # The plain script calls the model from this process; the workflow's worker has its own client.
        self.llm = llm
        self.ledger = Ledger(ledger)
        self.review_file = review_file
        self.worker: subprocess.Popen | None = None
        self.crash_after_pay = False
        # A fresh suffix per desk session, so a refund can be started again after a reset.
        self.run = uuid.uuid4().hex[:6]
        self.started: dict[str, str] = {}
        self.lock = threading.Lock()

    def call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=60)

    def start_worker(self, crash_after_pay: bool = False) -> None:
        with self.lock:
            if self.worker and self.worker.poll() is None:
                return
            self.crash_after_pay = crash_after_pay
            args = [sys.executable, "-c", "from refunds.cli import main; main()", "worker", "--address", self.address, "--ledger", str(self.ledger.path),
                    "--queue", DESK_QUEUE]
            self.worker = subprocess.Popen(args + (["--crash-after-pay"] if crash_after_pay else []), start_new_session=True)

    def kill_worker(self) -> None:
        # SIGKILL, as the kernel's out-of-memory killer or a pulled plug would: no cleanup runs.
        with self.lock:
            if self.worker and self.worker.poll() is None:
                os.killpg(self.worker.pid, signal.SIGKILL)
                self.worker.wait()

    def stop_worker(self) -> None:
        if self.worker and self.worker.poll() is None:
            self.worker.terminate()

    def worker_state(self) -> dict:
        alive = bool(self.worker and self.worker.poll() is None)
        return {"alive": alive, "pid": self.worker.pid if alive else None, "crash_after_pay": self.crash_after_pay}

    def start(self, refund: str) -> None:
        flow_id = workflow_id(refund, self.run)
        self.call(self.client.start_workflow(RefundWorkflow.run, refund, id=flow_id, task_queue=DESK_QUEUE))
        self.started[refund] = flow_id

    def verdict(self, refund: str, reject: bool, manager: str) -> None:
        handle = self.client.get_workflow_handle(self.started[refund])
        self.call(handle.signal(RefundWorkflow.reject if reject else RefundWorkflow.approve, manager))

    def plain(self, refund: str) -> None:
        """The plain script with a manager's yes already recorded: it pays, crashes, and the operator runs it again."""
        llm, model = self.llm()
        self.ledger.record_verdict(refund, "approved", MANAGER)
        try:
            naive.run(refund, self.ledger, llm, model, crash_after_pay=True, crash=raise_crash, poll=0.05)
        except Crash:
            naive.run(refund, self.ledger, llm, model, poll=0.05)

    def reset(self) -> None:
        self.ledger.clear()
        for marker in (self.ledger.path.parent / "crashed").glob("*"):
            marker.unlink()
        self.run, self.started = uuid.uuid4().hex[:6], {}

    async def flow(self, refund: str, flow_id: str) -> dict:
        handle = self.client.get_workflow_handle(flow_id)
        history = await handle.fetch_history()
        return {"id": flow_id, "status": status(history), "events": timeline(history)}

    def desk(self) -> dict:
        async def all_flows():
            got = await asyncio.gather(*(self.flow(refund, flow_id) for refund, flow_id in self.started.items()), return_exceptions=True)
            return {refund: (outcome if not isinstance(outcome, BaseException) else {"id": self.started[refund], "error": str(outcome)}) for refund, outcome in zip(self.started, got)}

        known = orders()
        return {
            "refunds": [{"id": refund_request.id, "received": refund_request.received, "message": refund_request.message, "order": known[refund_request.order]} for refund_request in requests()],
            "flows": self.call(all_flows()) if self.started else {},
            "worker": self.worker_state(),
            "payouts": self.ledger.payouts(),
            "calls": self.ledger.calls("durable"),
            "review": json.loads(self.review_file.read_text()) if self.review_file.exists() else None,
            "deadline_days": APPROVAL_DEADLINE.days,
            "manager": MANAGER,
        }


def handler(room: Room) -> type[BaseHTTPRequestHandler]:
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
            """Only this machine's own page may call: a rebound Host or a foreign Origin could pay out refunds."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            if self.path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/desk":
                return self.send_json(room.desk())
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            data = self.body()
            refund = str(data.get("refund", ""))
            known = {refund_request.id for refund_request in requests()}
            if self.path == "/api/start":
                if refund not in known:
                    return self.send(400, b"one of the refund requests", "text/plain")
                if refund not in room.started:
                    room.start(refund)
                return self.send_json(room.desk())
            if self.path == "/api/verdict":
                if refund not in room.started:
                    return self.send(400, b"a started refund", "text/plain")
                room.verdict(refund, bool(data.get("reject")), str(data.get("manager") or MANAGER))
                return self.send_json(room.desk())
            if self.path == "/api/plain":
                if refund not in known or room.llm is None:
                    return self.send(400, b"one of the refund requests, with a model configured", "text/plain")
                room.plain(refund)
                return self.send_json(room.desk())
            if self.path == "/api/worker/start":
                room.start_worker(bool(data.get("crash_after_pay")))
                return self.send_json(room.desk())
            if self.path == "/api/worker/kill":
                room.kill_worker()
                return self.send_json(room.desk())
            if self.path == "/api/reset":
                room.reset()
                return self.send_json(room.desk())
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(room: Room, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(room))
