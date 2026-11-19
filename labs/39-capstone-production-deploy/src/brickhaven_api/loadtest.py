"""A burst load test with a hard deadline: who got served, who was limited, how fast, and what it cost.

The traffic is one runaway client sending half of all calls beside two honest ones. A refusal of the
runaway client is the service working; a failure for anyone else is an error. Cost per task is read from
the service's own ledger, after the run, so the number is what the service booked.
"""

import json
import math
import time
import tomllib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from brickhaven_api.config import DATA

RUNAWAY_CLIENT = "runaway-sync"
HONEST_CLIENTS = ("maple-portal", "harbor-sms")


def percentile(values: list[float], share: float) -> float:
    """Nearest-rank percentile: the value that `share` of the sample is at or below. Empty gives 0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def traffic_plan(total: int) -> list[str]:
    """Which client sends each call: every other call is the runaway client's, the rest alternate."""
    return [RUNAWAY_CLIENT if number % 2 == 0 else HONEST_CLIENTS[(number // 2) % 2] for number in range(total)]


@dataclass
class Outcome:
    client: str
    status: int
    first_token_ms: float | None = None
    served_by: str = ""


@dataclass
class LoadReport:
    mode: str
    requests: int
    outcomes: list[Outcome]
    timed_out: int
    total_cost: float
    breakers: dict = field(default_factory=dict)

    @property
    def served(self) -> list[Outcome]:
        return [outcome for outcome in self.outcomes if outcome.status == 200]

    @property
    def limited(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == 429 and outcome.client == RUNAWAY_CLIENT)

    @property
    def errors(self) -> int:
        """Anything that is not a served call or the runaway client's expected 429, plus calls that never finished."""
        return self.requests - len(self.served) - self.limited

    @property
    def error_rate(self) -> float:
        return self.errors / self.requests if self.requests else 0.0

    @property
    def p95_first_token_ms(self) -> float:
        return percentile([outcome.first_token_ms for outcome in self.served if outcome.first_token_ms is not None], 0.95)

    @property
    def cost_per_task(self) -> float:
        return self.total_cost / len(self.served) if self.served else 0.0

    def served_by(self, slot: str) -> int:
        return sum(1 for outcome in self.served if outcome.served_by == slot)

    def summary_lines(self) -> list[str]:
        return [f"{self.mode} load: {self.requests} requests, {len(self.served)} served, {self.limited} limited, {self.errors} errors",
                f"error rate {self.error_rate * 100:.1f}%",
                f"p95 first token {self.p95_first_token_ms:.0f} ms",
                f"cost per task ${self.cost_per_task:.6f}, total ${self.total_cost:.6f}",
                f"served by fallback: {self.served_by('fallback')}",
                f"breakers: {self.breakers}"]


def demo_keys() -> dict[str, str]:
    return tomllib.loads((DATA / "demo_keys.toml").read_text())["keys"]


def messages() -> list[str]:
    return [json.loads(line)["text"] for line in (DATA / "messages.jsonl").read_text().splitlines() if line.strip()]


def get_json(url: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def wait_until_up(api_url: str, seconds: float = 20) -> dict:
    deadline = time.monotonic() + seconds
    while True:
        try:
            return get_json(f"{api_url}/health", timeout=2)
        except OSError:
            if time.monotonic() > deadline:
                raise SystemExit(f"nothing answered at {api_url}/health within {seconds:.0f}s")
            time.sleep(0.3)


def send(api_url: str, client: str, key: str, text: str, timeout: float) -> Outcome:
    request = urllib.request.Request(f"{api_url}/v1/work-orders", json.dumps({"message": text}).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
            return Outcome(client, response.status, payload["first_token_ms"], payload["served_by"])
    except urllib.error.HTTPError as error:
        return Outcome(client, error.code)
    except (OSError, ValueError):
        return Outcome(client, 0)


def run_load(api_url: str, portal_url: str, total: int, concurrency: int, deadline_seconds: float,
             request_timeout: float = 30) -> LoadReport:
    """Send `total` calls at once-ish. Calls still running at the deadline are counted as errors, never waited for."""
    mode = wait_until_up(api_url)["mode"]
    keys, texts = demo_keys(), messages()
    pool = ThreadPoolExecutor(max_workers=concurrency)
    try:
        futures = [pool.submit(send, api_url, client, keys[client], texts[number % len(texts)], request_timeout)
                   for number, client in enumerate(traffic_plan(total))]
        done, pending = wait(futures, timeout=deadline_seconds)
        for future in pending:
            future.cancel()
        outcomes = [future.result() for future in done if not future.cancelled()]
    finally:
        # Do not wait for stuck calls: the deadline is hard.
        pool.shutdown(wait=False, cancel_futures=True)
    summary = get_json(f"{portal_url}/api/summary")
    return LoadReport(mode, total, outcomes, len(pending), summary["total_cost"], summary["breakers"])
