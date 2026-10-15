"""The provider's invoice: one line per generation, with the cost the provider billed for it.

OpenRouter returns the billed cost on every response when asked with `usage.include`, keyed by the
generation id. This module records those lines at the HTTP client, below the application and outside
OpenTelemetry, so the invoice holds every call the key paid for, traced or not.
"""

import json
import threading
from datetime import datetime, timezone

import httpx

from quillmark.config import PRICES


class Invoice:
    """Line items as the provider reports them. Finance reconciles against this total, never the traces'."""

    def __init__(self):
        self.lines: list[dict] = []
        self.lock = threading.Lock()

    def record(self, response: httpx.Response) -> None:
        """An httpx response hook: read the generation id and its billed cost off a chat completion."""
        if not response.url.path.endswith("/chat/completions") or response.status_code != 200:
            return
        response.read()
        body = response.json()
        usage = body.get("usage") or {}
        line = {"generation_id": body.get("id"), "model": body.get("model"),
                "input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0),
                "cost": billed_cost(usage, body.get("model", "")),
                "billed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}
        with self.lock:
            self.lines.append(line)

    def total(self) -> float:
        return sum(line["cost"] for line in self.lines)

    def http_client(self, transport: httpx.BaseTransport | None = None) -> httpx.Client:
        """The client the OpenAI SDK sends through; a test passes a mock transport instead of the network."""
        return httpx.Client(event_hooks={"response": [self.record]}, transport=transport, timeout=90)

    def save(self, path) -> None:
        path.write_text(json.dumps({"lines": self.lines, "total": self.total()}, indent=1))


def billed_cost(usage: dict, model: str) -> float:
    """The billed cost when the provider sent it; list prices only for a provider that does not."""
    if usage.get("cost") is not None:
        return float(usage["cost"])
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    price_in, price_cached, price_out = PRICES.get(model, (0.0, 0.0, 0.0))
    return ((usage.get("prompt_tokens", 0) - cached) * price_in + cached * price_cached
            + usage.get("completion_tokens", 0) * price_out) / 1e6
