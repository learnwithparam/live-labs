"""Fallback routing: try the primary model, and on failure or an open breaker use the second model.

Each model has its own breaker, so one provider's outage never blocks the other. The time a failed
attempt cost is added to the first-token time, because the tenant waited for it too.
"""

import time
from dataclasses import dataclass
from typing import Callable

from brickhaven_api.breaker import CircuitBreaker
from brickhaven_api.provider import Completion, ProviderError


class AllModelsDown(Exception):
    """Both slots failed or are skipped by an open breaker: the service says 503 and asks the caller to retry."""


@dataclass
class Routed:
    completion: Completion
    slot: str
    first_token_ms: float


class Router:
    def __init__(self, provider, primary: str, fallback: str, breakers: dict[str, CircuitBreaker],
                 clock: Callable[[], float] = time.perf_counter):
        self.provider = provider
        self.slots = (("primary", primary), ("fallback", fallback))
        self.breakers = breakers
        self.clock = clock

    def complete(self, system: str, user: str) -> Routed:
        started = self.clock()
        for slot, model in self.slots:
            breaker = self.breakers[model]
            if not breaker.allow():
                continue
            attempt_started = self.clock()
            try:
                completion = self.provider.complete(model, system, user)
            except ProviderError:
                breaker.record_failure()
                continue
            breaker.record_success()
            waited_ms = (attempt_started - started) * 1000
            return Routed(completion, slot, waited_ms + completion.first_token_ms)
        raise AllModelsDown("no model slot is available")

    def breaker_states(self) -> dict[str, str]:
        return {slot: self.breakers[model].state for slot, model in self.slots}
