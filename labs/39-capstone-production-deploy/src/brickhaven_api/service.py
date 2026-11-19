"""The hardened service: who is asking, are they within their limit, which model answers, what it cost.

The order of the checks is the design. Key first, because an unknown caller costs nothing to refuse.
Limit second, because a refused call must never reach the paid model. Only then the model, and the
ledger row is written before the answer is read, because the money is spent even when the answer is bad.
"""

import json
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from brickhaven_api.config import HARD_CEILING_PER_MINUTE
from brickhaven_api.keys import ClientKeys
from brickhaven_api.ledger import Ledger
from brickhaven_api.limiter import RateLimiter
from brickhaven_api.provider import ProviderError
from brickhaven_api.router import AllModelsDown, Routed, Router
from brickhaven_api.workorder import SYSTEM_PROMPT, WorkOrderError, parse_work_order

OPEN_CLIENT = "open"
MAX_MESSAGE_CHARS = 2000


@dataclass
class Reply:
    status: int
    payload: dict
    headers: dict = field(default_factory=dict)


class App:
    """All the state of one running service. `hardened=False` is the naive version: no key, no limit, no fallback."""

    def __init__(self, provider, router: Router, keys: ClientKeys, limiter: RateLimiter, ledger: Ledger,
                 hardened: bool = True, billing=None, changes_path: Path | None = None):
        self.provider, self.router, self.keys, self.limiter, self.ledger = provider, router, keys, limiter, ledger
        self.hardened, self.billing, self.changes_path = hardened, billing, changes_path
        self.usage: dict[str, dict[str, int]] = {}
        self.limit_changes: list[dict] = []
        self.lock = threading.Lock()
        for client in keys.clients:
            limiter.configure(client.name, client.per_minute, client.burst)

    def count(self, client: str, outcome: str) -> None:
        with self.lock:
            counts = self.usage.setdefault(client, {"served": 0, "limited": 0, "failed": 0})
            counts[outcome] += 1

    def handle_work_order(self, authorization: str, body: dict) -> Reply:
        request_id = uuid.uuid4().hex[:8]
        headers = {"X-Request-Id": request_id}
        client_name = OPEN_CLIENT
        if self.hardened:
            client = self.keys.authenticate(authorization)
            if client is None:
                return Reply(401, {"error": "missing or unknown API key"}, headers)
            client_name = client.name
            allowed, retry_after = self.limiter.take(client_name)
            if not allowed:
                self.count(client_name, "limited")
                return Reply(429, {"error": "rate limit reached, retry later"}, {**headers, "Retry-After": str(retry_after)})
        message = body.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE_CHARS:
            return Reply(400, {"error": f"message must be 1 to {MAX_MESSAGE_CHARS} characters"}, headers)
        try:
            routed = self.route(message)
        except AllModelsDown:
            self.count(client_name, "failed")
            return Reply(503, {"error": "the assistant is unavailable, retry later"}, {**headers, "Retry-After": "5"})
        except ProviderError:
            self.count(client_name, "failed")
            return Reply(502, {"error": "the assistant is unavailable"}, headers)
        self.ledger.record(client_name, routed.slot, routed.completion)
        try:
            work_order = parse_work_order(routed.completion.text)
        except WorkOrderError:
            self.count(client_name, "failed")
            return Reply(502, {"error": "the assistant did not return a work order"}, headers)
        self.count(client_name, "served")
        # The caller sees the slot and the time, never the model's name or the cost: those are the operator's.
        return Reply(200, {"work_order": work_order.to_dict(), "served_by": routed.slot,
                           "first_token_ms": round(routed.first_token_ms)}, headers)

    def route(self, message: str):
        if self.hardened:
            return self.router.complete(SYSTEM_PROMPT, message)
        # The naive path: one model, no breaker, no second chance.
        model = self.router.slots[0][1]
        completion = self.provider.complete(model, SYSTEM_PROMPT, message)
        return Routed(completion, "primary", completion.first_token_ms)

    def raise_limit(self, operator: str, client_name: str, per_minute: int, reason: str) -> dict:
        """The human in the loop: a named operator raises one client's limit, with a reason, up to the ceiling."""
        if not operator.strip() or len(reason.split()) < 4:
            raise ValueError("give your name and a reason of at least 4 words")
        before = self.keys.by_name(client_name)
        old = before.per_minute if before else None
        client = self.keys.raise_limit(client_name, per_minute)
        self.limiter.configure(client.name, client.per_minute, client.burst)
        change = {"operator": operator.strip(), "client": client_name, "from": old, "to": client.per_minute, "reason": reason.strip()}
        self.limit_changes.append(change)
        if self.changes_path:
            self.changes_path.parent.mkdir(parents=True, exist_ok=True)
            with self.changes_path.open("a") as handle:
                handle.write(json.dumps(change) + "\n")
        return change

    def summary(self) -> dict:
        """Everything the operator's portal shows. Model names stay out of it: the page shows slots."""
        costs = self.ledger.cost_by_client()
        clients = [{"name": client.name, "owner": client.owner, "per_minute": client.per_minute, "burst": client.burst,
                    "usage": self.usage.get(client.name, {"served": 0, "limited": 0, "failed": 0}),
                    "cost": costs.get(client.name, 0.0)} for client in self.keys.clients]
        rows = self.ledger.rows[-30:]
        served = sum(counts["served"] for counts in self.usage.values())
        return {"mode": "hardened" if self.hardened else "naive", "clients": clients,
                "ledger": [{key: row[key] for key in ("at", "client", "slot", "prompt_tokens", "completion_tokens", "cost")} for row in reversed(rows)],
                "total_cost": self.ledger.total(), "served": served, "cost_per_task": self.ledger.total() / served if served else 0.0,
                "breakers": self.router.breaker_states(), "limit_changes": self.limit_changes[-5:],
                "can_reconcile": self.billing is not None, "ceiling": HARD_CEILING_PER_MINUTE}
