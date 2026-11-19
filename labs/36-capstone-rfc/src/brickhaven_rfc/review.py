"""The review: is this RFC measurable, and does it fit the time the business signed for?

Sign-off needs four things. Every failure mode names a metric, a threshold and a tag, and the signed
benchmark has enough labelled cases under that tag. The steps' times add up to no more than the signed
p95. Every work order field has a pass-rate threshold. And no signed file changed after sign-off.
A person can also send a failure mode back; that note holds for one exact version of the RFC only.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from brickhaven_rfc.benchmark import cases_per_tag, changed_files, load_cases, load_signoff, signed_budget_ms
from brickhaven_rfc.config import DATA, MIN_CASES_PER_FAILURE_MODE, OUT
from brickhaven_rfc.rfc import Rfc

FIELDS = ("trade", "urgency", "unit", "safety_flag", "access_window")
SEND_BACKS = OUT / "send_backs.jsonl"


@dataclass
class ModeResult:
    id: str
    what: str
    metric: str
    threshold: float | None
    cases: int
    problems: list[str]


@dataclass
class Verdict:
    modes: list[ModeResult]
    steps: dict[str, int]
    signed_ms: int
    plan_gaps: list[str]
    changed: list[str]
    cases_by_tag: dict[str, int] = field(default_factory=dict)

    @property
    def unmeasured(self) -> list[ModeResult]:
        return [mode for mode in self.modes if mode.problems]

    @property
    def total_ms(self) -> int:
        return sum(self.steps.values())

    @property
    def approved(self) -> bool:
        return not (self.unmeasured or self.total_ms > self.signed_ms or self.plan_gaps or self.changed or not self.modes)


def load_send_backs(path: Path | None = None) -> list[dict]:
    path = path or SEND_BACKS  # looked up per call, so a test can point it at a scratch file
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def send_back(rfc: Rfc, mode_id: str, reviewer: str, reason: str, path: Path | None = None) -> None:
    path = path or SEND_BACKS
    if mode_id not in {mode.id for mode in rfc.failure_modes}:
        raise SystemExit(f"No failure mode {mode_id!r} in this RFC.")
    if not reviewer.strip() or not reason.strip():
        raise SystemExit("A send-back needs the reviewer's name and a reason.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as notes:
        notes.write(json.dumps({"digest": rfc.digest, "mode": mode_id, "reviewer": reviewer, "reason": reason}) + "\n")


def review(rfc: Rfc, directory: Path = DATA, send_backs: list[dict] | None = None, without_tag: str = "") -> Verdict:
    cases = [case for case in load_cases(directory) if without_tag not in case["tags"]]
    counts = cases_per_tag(cases)
    notes = [note for note in (load_send_backs() if send_backs is None else send_backs) if note["digest"] == rfc.digest]
    modes = []
    for mode in rfc.failure_modes:
        problems = [f"no {name}" for name, value in (("metric", mode.metric), ("threshold", mode.threshold), ("benchmark tag", mode.tag)) if value in ("", None)]
        if mode.tag and counts[mode.tag] < MIN_CASES_PER_FAILURE_MODE:
            problems.append(f"{counts[mode.tag]} labelled cases, needs {MIN_CASES_PER_FAILURE_MODE}")
        problems += [f"sent back by {note['reviewer']}: {note['reason']}" for note in notes if note["mode"] == mode.id]
        modes.append(ModeResult(mode.id, mode.what, mode.metric, mode.threshold, counts[mode.tag] if mode.tag else 0, problems))
    gaps = [name for name in FIELDS if name not in rfc.eval_plan]
    return Verdict(modes, rfc.budget_ms, signed_budget_ms(directory), gaps, changed_files(directory), dict(counts))


def summary(verdict: Verdict) -> list[str]:
    total, signed = verdict.total_ms, verdict.signed_ms
    lines = [f"  failure modes: {len(verdict.modes) - len(verdict.unmeasured)} of {len(verdict.modes)} measured"]
    lines.append(f"  time: steps add up to {total} ms, {'over' if total > signed else 'inside'} the signed {signed} ms")
    lines.append(f"  eval plan: {len(verdict.plan_gaps)} fields with no threshold" if verdict.plan_gaps else "  eval plan: every field has a threshold")
    lines += [f"  {name} changed after sign-off" for name in verdict.changed]
    word = "approved" if verdict.approved else "refused"
    return lines + [f"sign-off {word}: {len(verdict.unmeasured)} unmeasured failure modes"]
