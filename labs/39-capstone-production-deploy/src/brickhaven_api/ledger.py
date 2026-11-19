"""The cost ledger: one row per model call, costed from list prices, then checked against the provider's bill.

The ledger is the service's own book and the provider's billing record is the outside one. A ledger
that was only ever compared with itself would be wrong without anyone knowing; the reconcile step
compares the two and says how far apart they are.
"""

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from brickhaven_api.billing import Billing
from brickhaven_api.config import LEDGER_TOLERANCE, PRICES


def price_call(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Dollars for one call at list price; a model with no price is an error, never a free call."""
    input_price, output_price = PRICES[model]
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000


@dataclass
class Reconciliation:
    checked: int
    missing: list[str]
    ledger_total: float
    billing_total: float
    gap_share: float
    mismatched: list[str] = field(default_factory=list)

    @property
    def within_tolerance(self) -> bool:
        return self.checked > 0 and not self.missing and self.gap_share <= LEDGER_TOLERANCE


class Ledger:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.rows: list[dict] = []
        self.lock = threading.Lock()
        if path and path.exists():
            self.rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def record(self, client: str, slot: str, completion) -> dict:
        row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "client": client, "slot": slot,
               "model": completion.model, "generation_id": completion.generation_id,
               "prompt_tokens": completion.prompt_tokens, "completion_tokens": completion.completion_tokens,
               "cost": price_call(completion.model, completion.prompt_tokens, completion.completion_tokens)}
        with self.lock:
            self.rows.append(row)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as handle:
                    handle.write(json.dumps(row) + "\n")
        return row

    def total(self) -> float:
        return sum(row["cost"] for row in self.rows)

    def cost_by_client(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for row in self.rows:
            totals[row["client"]] = totals.get(row["client"], 0.0) + row["cost"]
        return totals


def reconcile(rows: list[dict], lookup: Callable[[str], Billing | None], tolerance: float = LEDGER_TOLERANCE) -> Reconciliation:
    """Compare each ledger row with the provider's record of the same generation.

    A row the provider has no record of is `missing`, and any missing row fails the check: money may
    have been spent that nobody can account for.
    """
    ledger_total = billing_total = 0.0
    missing, mismatched = [], []
    for row in rows:
        billing = lookup(row["generation_id"])
        if billing is None:
            missing.append(row["generation_id"])
            continue
        ledger_total += row["cost"]
        billing_total += billing.cost
        # A row is flagged when it is off by more than the tolerance, with a floor for rounding on tiny sums.
        if abs(row["cost"] - billing.cost) > max(tolerance * billing.cost, 0.00001):
            mismatched.append(row["generation_id"])
    gap_share = abs(ledger_total - billing_total) / billing_total if billing_total else (0.0 if not ledger_total else 1.0)
    return Reconciliation(len(rows) - len(missing), missing, ledger_total, billing_total, gap_share, mismatched)
