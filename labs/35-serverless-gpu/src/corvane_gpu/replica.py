"""One GPU replica's life under scale to zero: start on demand, serve the burst, idle, then shut down.

This is what Modal does for each container, run by hand so every second is measured. The run starts
the real vLLM server on a GPU, or a stand-in for rehearsal, and writes its results after every phase,
so a run cut off by its time guard still leaves a file to read.
"""

import json
import os
import secrets
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from corvane_gpu.burst import TIMED_OUT, Endpoint, Outcome, ask, send_burst
from corvane_gpu.economics import wake_cost


@dataclass
class Plan:
    server_command: list[str]
    port: int
    waves: list[tuple[str, list[dict]]]
    examples: list[dict]
    served_name: str
    out_dir: Path
    concurrency: int
    request_timeout: float
    cold_start_timeout: float
    idle_seconds: float
    deadline: float
    price_per_second: float
    # The seconds a wave needs to be worth starting: a cold start, a short burst and the idle window.
    wave_minimum_seconds: float = 150
    extra_body: dict = field(default_factory=dict)
    max_tokens: int | None = 8
    about: dict = field(default_factory=dict)


class Replica:
    """The server process. Its API key reaches it through the environment, never the command line."""

    def __init__(self, command: list[str], port: int, api_key: str, log_path: Path):
        self.command, self.port, self.api_key, self.log_path = command, port, api_key, log_path
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        log = self.log_path.open("ab")
        environment = dict(os.environ, VLLM_API_KEY=self.api_key)
        # Its own process group, so stopping it also stops the engine worker vLLM forks to hold the GPU.
        self.process = subprocess.Popen(self.command, stdout=log, stderr=subprocess.STDOUT, env=environment,
                                        start_new_session=True)

    def wait_ready(self, timeout: float) -> bool:
        """Poll /health until the model is loaded. vLLM answers it only once it can serve a request."""
        until = time.time() + timeout
        while time.time() < until:
            if self.process.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as response:
                    if response.status == 200:
                        return True
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        return False

    def stop(self) -> None:
        if not self.process:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()
        except ProcessLookupError:
            pass


def status_without_key(port: int, api_key: str | None) -> int:
    """The HTTP status a caller gets with no key or a wrong one. Anything but 401 is an open endpoint."""
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/models")
    if api_key is not None:
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except OSError:
        return 0


def failed_items(items: list[dict], reason: str, at: float) -> list[dict]:
    # Fail closed: an item the endpoint never answered is a failure in the results, not a gap in them.
    return [Outcome(item["id"], TIMED_OUT, "", None, at, 0.0, error=reason).as_dict() for item in items]


def write_results(results: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    partial = out_dir / "results.json.partial"
    partial.write_text(json.dumps(results, indent=1))
    partial.replace(out_dir / "results.json")


def run_waves(plan: Plan) -> dict:
    """Each wave of requests finds the endpoint at zero replicas, wakes it, and lets it scale back to zero.

    The first wave is the golden set. A later wave shows the cold start again with the weights already
    on disk, which is what a Modal volume gives every new container.
    """
    clock_zero = time.time()
    plan.out_dir.mkdir(parents=True, exist_ok=True)
    api_key = secrets.token_urlsafe(24)
    timeline = [{"at": 0.0, "replicas": 0, "event": "no traffic, no replica"}]
    results = {"about": plan.about, "timeline": timeline, "waves": [], "auth": {}, "guard_hit": False,
               "settings": {"concurrency": plan.concurrency, "request_timeout": plan.request_timeout,
                            "cold_start_timeout": plan.cold_start_timeout, "idle_seconds": plan.idle_seconds}}
    endpoint = Endpoint(f"http://127.0.0.1:{plan.port}/v1", api_key, plan.served_name, plan.extra_body, plan.max_tokens)

    def now() -> float:
        return round(time.time() - clock_zero, 3)

    for name, items in plan.waves:
        if plan.deadline - time.time() < plan.wave_minimum_seconds:
            results["guard_hit"] = True
            results["waves"].append({"name": name, "items": len(items), "started": False,
                                     "reason": "not started: the time guard left too little time for a cold start"})
            break
        replica = Replica(plan.server_command, plan.port, api_key, plan.out_dir / "server.log")
        wave = {"name": name, "items": len(items), "started": True}
        results["waves"].append(wave)
        woke = time.time()
        timeline.append({"at": now(), "replicas": 1, "event": f"{name}: requests arrive, a replica starts"})
        replica.start()
        try:
            ready = replica.wait_ready(min(plan.cold_start_timeout, plan.deadline - time.time()))
            wave["cold_start_seconds"] = round(time.time() - woke, 2)
            if not ready:
                # A replica that never came up fails every item that was waiting on it.
                wave["outcomes"] = failed_items(items, f"cold start did not finish in {wave['cold_start_seconds']:.0f} s", now())
                wave["billing"] = wake_cost(plan.price_per_second, wave["cold_start_seconds"], 0.0, 0.0)
                timeline.append({"at": now(), "replicas": 0, "event": f"{name}: cold start failed"})
                results["guard_hit"] = time.time() >= plan.deadline
                write_results(results, plan.out_dir)
                continue
            timeline.append({"at": now(), "replicas": 1, "event": f"{name}: ready after {wave['cold_start_seconds']:.1f} s"})
            if not results["auth"]:
                results["auth"] = {"no_key": status_without_key(plan.port, None),
                                   "wrong_key": status_without_key(plan.port, "not-the-key"),
                                   "right_key": status_without_key(plan.port, api_key)}
            busy_from = time.time()
            smoke = ask(endpoint, items[0], plan.examples, plan.request_timeout, clock_zero)
            wave["smoke"] = smoke.as_dict()
            write_results(results, plan.out_dir)
            if smoke.status != "ok":
                wave["outcomes"] = failed_items(items, f"smoke request failed: {smoke.error}", now())
            else:
                outcomes = send_burst(endpoint, items, plan.examples, plan.concurrency, plan.request_timeout,
                                      plan.deadline, clock_zero)
                wave["outcomes"] = [outcome.as_dict() for outcome in outcomes]
            busy_seconds = time.time() - busy_from
            timeline.append({"at": now(), "replicas": 1, "event": f"{name}: burst done, idle window starts"})
            write_results(results, plan.out_dir)
            # The replica waits out the scaledown window in case more work arrives, and that wait is billed.
            idle_from = time.time()
            time.sleep(max(0.0, min(plan.idle_seconds, plan.deadline - time.time())))
            idle_seconds = time.time() - idle_from
        finally:
            replica.stop()
        timeline.append({"at": now(), "replicas": 0, "event": f"{name}: idle window over, scaled to zero"})
        wave["billing"] = wake_cost(plan.price_per_second, wave["cold_start_seconds"], busy_seconds, idle_seconds)
        write_results(results, plan.out_dir)
    results["run_seconds"] = now()
    write_results(results, plan.out_dir)
    return results
