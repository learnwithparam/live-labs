"""Fakes that stand in for the provider, its billing record and the clock, so every rule runs with no key."""

import json

from brickhaven_api.billing import Billing
from brickhaven_api.provider import Completion, ProviderError

GOOD_ANSWER = json.dumps({"trade": "plumbing", "urgency": "routine", "unit": "4B", "safety_flag": False, "access_window": None})


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeProvider:
    """Answers with a fixed work order. Models in `failing` raise on demand; every call is recorded."""

    def __init__(self, failing=None, answer: str = GOOD_ANSWER, first_token_ms: float = 120.0):
        self.failing = set(failing or [])
        self.answer = answer
        self.first_token_ms = first_token_ms
        self.calls: list[str] = []

    def complete(self, model: str, system: str, user: str) -> Completion:
        self.calls.append(model)
        if model in self.failing:
            raise ProviderError("down on demand")
        return Completion(self.answer, f"gen-{len(self.calls)}", model, 1000, 200, self.first_token_ms)


class FakeBilling:
    """A billing record per generation id. `skew` multiplies every cost, to model a ledger that drifted."""

    def __init__(self, ledger_rows: list[dict], skew: float = 1.0, forget: set[str] | None = None):
        self.records = {row["generation_id"]: Billing(row["cost"] * skew, row["prompt_tokens"], row["completion_tokens"]) for row in ledger_rows
                        if row["generation_id"] not in (forget or set())}

    def lookup(self, generation_id: str):
        return self.records.get(generation_id)
