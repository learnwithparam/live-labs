"""Score each field of the work orders and decide whether a candidate may ship.

One overall score hides a dropped safety flag: the average can rise while the one field that must
never fail gets worse. So the gate reads three things: the pass rate of every field, the threshold
of every field, and each single case that passed on the live release and now fails.
"""

from dataclasses import dataclass, field

from brickhaven_evals.config import THRESHOLDS

FIELDS = tuple(THRESHOLDS)


def field_values(output: dict, field_name: str) -> list[str]:
    """The values of one field across a message's work orders, sorted so the order of orders is free.

    A message with two problems must produce two orders: a dropped order shortens this list and fails.
    """
    return sorted(str(order.get(field_name)) for order in output.get("work_orders", []))


def field_passes(case: dict, output: dict) -> dict[str, bool]:
    return {name: field_values(case["expected"], name) == field_values(output, name) for name in FIELDS}


def pass_rates(cases: list[dict], run: dict[str, dict]) -> dict[str, float]:
    """Share of cases whose field matches the verified answer. A case with no output fails every field."""
    counts = dict.fromkeys(FIELDS, 0)
    for case in cases:
        verdicts = field_passes(case, run.get(case["id"], {}))
        for name in FIELDS:
            counts[name] += verdicts[name]
    return {name: round(counts[name] / len(cases), 4) for name in FIELDS}


def overall_exact_match(cases: list[dict], run: dict[str, dict]) -> float:
    """The naive score: the share of cases with every field right, as one number."""
    right = sum(all(field_passes(case, run.get(case["id"], {})).values()) for case in cases)
    return round(right / len(cases), 4)


@dataclass
class Regression:
    case_id: str
    field_name: str
    message: str


@dataclass
class Verdict:
    live_rates: dict[str, float]
    candidate_rates: dict[str, float]
    below_threshold: list[str]
    regressions: list[Regression] = field(default_factory=list)
    naive_live: float = 0.0
    naive_candidate: float = 0.0

    @property
    def ships(self) -> bool:
        return not self.below_threshold and not self.regressions

    @property
    def naive_ships(self) -> bool:
        return self.naive_candidate >= self.naive_live


def gate(cases: list[dict], live_run: dict[str, dict], candidate_run: dict[str, dict]) -> Verdict:
    """Ship only when every field meets its threshold and no case got worse than the live release."""
    candidate_rates = pass_rates(cases, candidate_run)
    below = [name for name in FIELDS if candidate_rates[name] < THRESHOLDS[name]]
    regressions = []
    for case in cases:
        before = field_passes(case, live_run.get(case["id"], {}))
        after = field_passes(case, candidate_run.get(case["id"], {}))
        # A field that was right on the live release and is wrong now is a regression, even when the
        # candidate's rate for that field still clears its threshold.
        regressions += [Regression(case["id"], name, case["message"]) for name in FIELDS if before[name] and not after[name]]
    return Verdict(live_rates=pass_rates(cases, live_run), candidate_rates=candidate_rates, below_threshold=below,
                   regressions=regressions, naive_live=overall_exact_match(cases, live_run),
                   naive_candidate=overall_exact_match(cases, candidate_run))
