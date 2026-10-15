"""A stand-in for the Lambda service's function fleet: it receives invokes and runs the durable handler.

On AWS, Lambda starts a fresh environment for each invoke and you never see a server. Locally, this one
process plays that part for the SDK's runner, so killing it is a real crash of every job in flight.
"""

import json
import os
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aws_durable_execution_sdk_python_testing.invoker import create_test_lambda_context

from meterline import ledger
from meterline.job import handler, state_file


def arm_crash(after_writes: int) -> None:
    """The crash drill. After the Nth ledger row commits, the whole process gets SIGKILL, as a host
    failure would deliver it: no cleanup, no checkpoint, every in-flight job cut off mid-step.
    """
    committed: list[str] = []

    def maybe_crash(customer_id: str) -> None:
        committed.append(customer_id)
        if len(committed) == after_writes:
            os.kill(os.getpid(), signal.SIGKILL)

    ledger.after_commit_hooks.append(maybe_crash)


class Invocations(BaseHTTPRequestHandler):
    """Answers the Lambda Invoke API path the runner calls: POST /2015-03-31/functions/<name>/invocations."""

    def do_POST(self) -> None:
        event = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        # Every invoke is logged with this process id, so the bill can count billed seconds per process.
        invocation_id = ledger.start_invocation(state_file(), event.get("DurableExecutionArn", ""), "")
        output = handler(event, create_test_lambda_context())
        ledger.finish_invocation(state_file(), invocation_id)
        body = json.dumps(output).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def main() -> None:
    crash_after = int(os.environ.get("CRASH_AFTER_WRITES") or 0)
    if crash_after:
        arm_crash(crash_after)
    ThreadingHTTPServer(("127.0.0.1", int(os.environ["HOST_PORT"])), Invocations).serve_forever()


if __name__ == "__main__":
    main()
