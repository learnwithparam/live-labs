"""The hosted model on the same golden set, through the same burst code, so both sides are measured alike.

The hosted model has no replica to wake and no idle window to pay for. It bills per token, so its
cost per item is the same at ten items or ten thousand. That is why it wins at low volume.
"""

import json
import time
from pathlib import Path

from corvane_gpu.burst import Endpoint, send_burst
from corvane_gpu.config import HOSTED_CONCURRENCY, PRICES

# A hosted call waits on the internet and on a busy provider, so it gets longer than the GPU's budget.
HOSTED_TIMEOUT_SECONDS = 60
# OpenRouter puts what it billed for each call in the usage block when asked to.
BILLED_COST = {"usage": {"include": True}}


def item_cost(outcome: dict, model: str) -> float:
    """What one call cost. The provider's bill is the truth; list prices are the fallback.

    A failed call still counts at whatever tokens it used, which for a timeout is nothing reported.
    """
    if outcome["cost"] is not None:
        return float(outcome["cost"])
    price_in, _, price_out = PRICES.get(model, (0.0, 0.0, 0.0))
    return (outcome["prompt_tokens"] * price_in + outcome["completion_tokens"] * price_out) / 1e6


def run_hosted(items: list[dict], examples: list[dict], base_url: str, api_key: str, model: str,
               timeout: float = HOSTED_TIMEOUT_SECONDS, concurrency: int = HOSTED_CONCURRENCY) -> dict:
    endpoint = Endpoint(base_url, api_key, model, BILLED_COST)
    started = time.time()
    outcomes = [outcome.as_dict() for outcome in send_burst(endpoint, items, examples, concurrency, timeout,
                                                            clock_zero=started)]
    return {"model": model, "base_url": base_url, "concurrency": concurrency, "request_timeout": timeout,
            "run_seconds": round(time.time() - started, 2), "outcomes": outcomes,
            "total_cost": round(sum(item_cost(outcome, model) for outcome in outcomes), 6)}


def save(run: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run, indent=1))
