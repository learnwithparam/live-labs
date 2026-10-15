"""Send a burst of golden items to any OpenAI-compatible endpoint, many at once, and time each one.

The same code calls the served model on the GPU and the hosted model over the internet, so the two
sets of numbers are measured the same way. It uses only the standard library, because it also runs
inside the GPU notebook, where nothing else is installed for it.
"""

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

from corvane_gpu.golden import messages_for, parse_label

OK, TIMED_OUT, ERROR = "ok", "timed_out", "error"


@dataclass
class Endpoint:
    base_url: str
    api_key: str
    model: str
    # Extra request fields one side needs: Qwen3's switch to answer without thinking, or OpenRouter's billed cost.
    extra_body: dict = field(default_factory=dict)
    max_tokens: int | None = None


@dataclass
class Outcome:
    id: str
    status: str
    answer: str
    label: str | None
    started_at: float
    latency_seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def post_json(url: str, body: dict, api_key: str | None, timeout: float) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    if api_key is not None:
        request.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def ask(endpoint: Endpoint, item: dict, examples: list[dict], timeout: float, clock_zero: float) -> Outcome:
    """One item, one request. A request that runs out of time is a failed item, never a skipped one.

    The server sends nothing until the answer is complete, so the socket timeout is the request's whole
    budget. Whatever goes wrong, the item comes back with a status, so the count of items never shrinks.
    """
    body = {"model": endpoint.model, "messages": messages_for(item, examples), "temperature": 0,
            **endpoint.extra_body}
    if endpoint.max_tokens:
        body["max_tokens"] = endpoint.max_tokens
    started = time.time()
    try:
        reply = post_json(endpoint.base_url.rstrip("/") + "/chat/completions", body, endpoint.api_key, timeout)
    except TimeoutError:
        return Outcome(item["id"], TIMED_OUT, "", None, started - clock_zero, time.time() - started,
                       error=f"no answer in {timeout:.0f} s")
    except urllib.error.URLError as error:
        status = TIMED_OUT if isinstance(error.reason, TimeoutError) else ERROR
        detail = f"no answer in {timeout:.0f} s" if status == TIMED_OUT else str(error)[:160]
        return Outcome(item["id"], status, "", None, started - clock_zero, time.time() - started, error=detail)
    except (OSError, ValueError) as error:
        return Outcome(item["id"], ERROR, "", None, started - clock_zero, time.time() - started,
                       error=f"{type(error).__name__}: {str(error)[:140]}")
    latency = time.time() - started
    answer = (reply["choices"][0]["message"].get("content") or "").strip()
    usage = reply.get("usage") or {}
    return Outcome(item["id"], OK, answer, parse_label(answer), started - clock_zero, latency,
                   usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), usage.get("cost"))


def send_burst(endpoint: Endpoint, items: list[dict], examples: list[dict], concurrency: int, timeout: float,
               deadline: float | None = None, clock_zero: float | None = None) -> list[Outcome]:
    """Every item, `concurrency` at a time, in the golden set's order.

    Past the deadline, items not yet sent are failed with that reason, so a run cut short by its guard
    still reports every item, and the failures count against the endpoint.
    """
    clock_zero = time.time() if clock_zero is None else clock_zero

    def one(item: dict) -> Outcome:
        if deadline is not None and time.time() >= deadline:
            return Outcome(item["id"], TIMED_OUT, "", None, time.time() - clock_zero, 0.0,
                           error="the run's time guard ended the burst before this item was sent")
        budget = timeout if deadline is None else max(1.0, min(timeout, deadline - time.time()))
        return ask(endpoint, item, examples, budget, clock_zero)

    with ThreadPoolExecutor(concurrency) as pool:
        return list(pool.map(one, items))
