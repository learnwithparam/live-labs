"""The gateway's books: one ledger row per model call, and each team's spend against its monthly budget.

Spend is the invoice so far this month plus every cost the gateway booked since, and that cost is the
one OpenRouter reported for the call. A budget built on an estimate drifts from the bill, and the
bill is what finance holds the platform team to.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("FERNWAY_DATA", "data"))


def state_dir() -> Path:
    # The proxy and the CLI are separate processes; they share state through these files.
    return Path(os.environ.get("FERNWAY_STATE", "out"))


class Ledger:
    """Append-only JSON lines. The proxy writes, the CLI and the console read."""

    lock = threading.Lock()

    def __init__(self, path: Path | None = None):
        self.path = path or state_dir() / "ledger.jsonl"

    def add(self, row: dict) -> dict:
        row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **row}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, self.path.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
        return row

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]


class Budgets:
    """Each team's budget, what it has spent, and the admin's decisions on breaches."""

    def __init__(self, ledger: Ledger | None = None, decisions_path: Path | None = None):
        self.ledger = ledger or Ledger()
        self.decisions_path = decisions_path or state_dir() / "decisions.json"
        self.teams = json.loads((data_dir() / "teams.json").read_text())["teams"]
        self.invoice = json.loads((data_dir() / "invoices.json").read_text())

    def decisions(self) -> dict:
        if not self.decisions_path.exists():
            return {"budgets": {}, "routes": {}}
        return json.loads(self.decisions_path.read_text())

    def save_decisions(self, decisions: dict) -> None:
        self.decisions_path.parent.mkdir(parents=True, exist_ok=True)
        self.decisions_path.write_text(json.dumps(decisions, indent=1))

    def budget(self, team: str) -> float:
        # A raise is a person's decision, so it is stored apart from the budget finance set.
        raised = self.decisions()["budgets"].get(team)
        return raised["budget"] if raised else self.teams[team]["budget"]

    def spent(self, team: str) -> float:
        booked = sum(row.get("cost") or 0.0 for row in self.ledger.rows() if row.get("team") == team)
        return self.invoice["spend"].get(team, 0.0) + booked

    def allows(self, team: str) -> tuple[bool, str]:
        """Whether one more call may go out. Refuse at the budget: never send it to a cheaper model instead."""
        spent, budget = self.spent(team), self.budget(team)
        if spent < budget:
            return True, ""
        name = self.teams[team]["name"]
        return False, (f"Team {name} is over its monthly budget: ${spent:,.4f} spent of ${budget:,.2f}. "
                       "The gateway refused this call instead of sending it to a cheaper model. "
                       "Ask the platform team to raise the budget.")

    def raise_budget(self, team: str, new_budget: float, decided_by: str) -> None:
        decisions = self.decisions()
        decisions["budgets"][team] = {"budget": new_budget, "by": decided_by,
                                      "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.save_decisions(decisions)

    def label_route(self, call_id: str, tier: str, decided_by: str) -> None:
        """A person's answer to a flagged call: the tier it really needed. These become router training data."""
        decisions = self.decisions()
        decisions["routes"][call_id] = {"tier": tier, "by": decided_by,
                                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.save_decisions(decisions)

    def keep_refused(self, team: str, decided_by: str) -> None:
        # Saying no is a decision too: the console stops asking, and the record shows who said it.
        decisions = self.decisions()
        decisions.setdefault("held", {})[team] = {"by": decided_by,
                                                  "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.save_decisions(decisions)
