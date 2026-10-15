"""The local stand-in for AWS: the SDK's durable execution runner plus the function host, as two processes.

The runner is AWS's own open-source local runner. It stores checkpoints in SQLite, invokes the host the
way Lambda would, retries a failed invoke after 5 seconds, and serves the same Lambda APIs boto3 calls
in the cloud: start an execution, read its history, send a callback.
"""

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from functools import cached_property
from pathlib import Path
from urllib.request import Request, urlopen

import boto3

from meterline.config import FUNCTION_NAME

# Long enough for a held job to wait a full day for finance; Lambda allows up to a year.
EXECUTION_TIMEOUT_SECONDS = 2 * 24 * 3600


def wait_for_port(port: int, seconds: float = 20) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"Nothing is listening on port {port} after {seconds} seconds.")


class LocalCloud:
    """Starts, crashes and stops the two processes. Ports and files are arguments so tests run beside a demo."""

    def __init__(self, out: Path, runner_port: int, host_port: int):
        self.out, self.runner_port, self.host_port = out, runner_port, host_port
        self.runner: subprocess.Popen | None = None
        self.host: subprocess.Popen | None = None
        self.endpoint = f"http://127.0.0.1:{runner_port}"
        # Placeholder credentials keep boto3 from searching the machine for real AWS ones. Nothing here
        # talks to AWS: every Lambda call goes to the runner's endpoint.
        self.local_aws = {"AWS_ENDPOINT_URL_LAMBDA": self.endpoint, "AWS_ACCESS_KEY_ID": "local",
                          "AWS_SECRET_ACCESS_KEY": "local", "AWS_DEFAULT_REGION": "us-west-2"}

    def start_runner(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        runner_binary = Path(sys.executable).with_name("dex-local-runner")
        self.runner = subprocess.Popen(
            [str(runner_binary), "start-server", "--host", "127.0.0.1", "--port", str(self.runner_port),
             "--lambda-endpoint", f"http://127.0.0.1:{self.host_port}", "--store-type", "sqlite",
             "--store-path", str(self.out / "durable.db"), "--local-runner-endpoint", self.endpoint],
            env={**os.environ, **self.local_aws}, stdout=open(self.out / "runner.log", "a"), stderr=subprocess.STDOUT)
        wait_for_port(self.runner_port)

    def start_host(self, crash_after_writes: int = 0) -> int:
        """A fresh host process, the way Lambda starts a fresh environment after one dies."""
        # The SDK inside the handler checkpoints to whatever Lambda endpoint it is given: here, the runner.
        environment = {**os.environ, **self.local_aws, "HOST_PORT": str(self.host_port), "METERLINE_DB": str(self.out / "meterline.db"),
                       "CRASH_AFTER_WRITES": str(crash_after_writes)}
        self.host = subprocess.Popen([sys.executable, "-m", "meterline.host"], env=environment,
                                     stdout=open(self.out / "host.log", "a"), stderr=subprocess.STDOUT)
        wait_for_port(self.host_port)
        return self.host.pid

    def host_died(self) -> bool:
        return self.host is not None and self.host.poll() is not None

    def stop(self) -> None:
        for process in (self.host, self.runner):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(10)

    @cached_property
    def lambda_api(self):
        return boto3.client("lambda", endpoint_url=self.endpoint, region_name="us-west-2",
                            aws_access_key_id="local", aws_secret_access_key="local")

    def start_execution(self, customer_id: str, run_name: str) -> str:
        """StartDurableExecution, as the nightly EventBridge schedule would call it once per customer."""
        payload = {"AccountId": "123456789012", "FunctionName": FUNCTION_NAME, "FunctionQualifier": "$LATEST",
                   "ExecutionName": f"{run_name}-{customer_id}", "ExecutionTimeoutSeconds": EXECUTION_TIMEOUT_SECONDS,
                   "ExecutionRetentionPeriodDays": 7, "InvocationId": str(uuid.uuid4()),
                   "Input": json.dumps({"customer_id": customer_id})}
        request = Request(f"{self.endpoint}/start-durable-execution", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read())["ExecutionArn"]

    def status(self, execution_arn: str) -> dict:
        return self.lambda_api.get_durable_execution(DurableExecutionArn=execution_arn)

    def history(self, execution_arn: str) -> list[dict]:
        events, marker = [], None
        while True:
            page = self.lambda_api.get_durable_execution_history(
                DurableExecutionArn=execution_arn, IncludeExecutionData=True, **({"Marker": marker} if marker else {}))
            events += page.get("Events", [])
            marker = page.get("NextMarker")
            if not marker:
                return events

    def answer_callback(self, callback_id: str, answer: dict) -> None:
        """What finance's approve or reject button sends: the job resumes from exactly where it waited."""
        self.lambda_api.send_durable_execution_callback_success(CallbackId=callback_id, Result=json.dumps(answer).encode())
