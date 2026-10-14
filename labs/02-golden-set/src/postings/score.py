"""The golden set, the per-field score of one run against it, and the gate between two runs."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from postings.schema import FIELDS, same


def load_golden(path: Path) -> dict[str, dict]:
    """posting file name -> the fields a person approved for it."""
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {row["posting"]: row["approved"] for row in rows}


def right(golden: dict[str, dict], readings: dict[str, dict]) -> dict[str, dict[str, bool]]:
    """posting -> field -> whether the run agrees with the approved value."""
    return {name: {f: same(f, readings[name][f], approved[f]) for f in FIELDS} for name, approved in golden.items()}


def accuracy(golden: dict[str, dict], readings: dict[str, dict]) -> dict[str, int]:
    """field -> how many postings the run got right. The number the team watches, one per field."""
    marks = right(golden, readings)
    # Count down each column, not across each row: "salary_min 12 of 14" says which filter broke.
    return {f: sum(marks[name][f] for name in golden) for f in FIELDS}


@dataclass
class Change:
    posting: str
    field: str
    approved: object
    before: object
    after: object


@dataclass
class Verdict:
    before: dict[str, int]
    after: dict[str, int]
    total: int
    regressed: list[Change] = field(default_factory=list)
    fixed: list[Change] = field(default_factory=list)

    @property
    def worse(self) -> list[str]:
        # One field down is enough. A better total does not pay for a salary filter that hides jobs.
        return [f for f in FIELDS if self.after[f] < self.before[f]]

    @property
    def blocked(self) -> bool:
        return bool(self.worse)

    @property
    def needs_person(self) -> list[str]:
        """Postings a field went from right to wrong on: a person reads each and decides who is wrong."""
        return sorted({c.posting for c in self.regressed})


def gate(golden: dict[str, dict], before: dict[str, dict], after: dict[str, dict]) -> Verdict:
    """A change ships only when no field gets worse. Every posting it broke or fixed is listed."""
    was, now = right(golden, before), right(golden, after)
    verdict = Verdict(accuracy(golden, before), accuracy(golden, after), len(golden))
    for name, approved in golden.items():
        for f in FIELDS:
            change = Change(name, f, approved[f], before[name][f], after[name][f])
            # Right before and wrong after: the change broke this field on this posting.
            if was[name][f] and not now[name][f]:
                verdict.regressed.append(change)
            # Wrong before and right after: the change fixed it.
            elif now[name][f] and not was[name][f]:
                verdict.fixed.append(change)
    return verdict


def save_run(path: Path, prompt: str, model: str, readings: dict[str, dict], cost: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"prompt": prompt, "model": model, "cost": cost, "readings": readings}, indent=1))


def load_run(path: Path) -> dict:
    return json.loads(path.read_text())
