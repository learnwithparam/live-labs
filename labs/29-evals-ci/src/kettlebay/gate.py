"""The release gate: run the golden set on a release, compare it with the live release, block or pass.

The gate fails closed. A run that errors or times out, a golden case with no result, or a live run
that cannot be compared all block the release. Only a person can let a blocked release through,
with a written reason, and that override is recorded against the exact release it was given for.
"""

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from kettlebay.assistant import Release, converse
from kettlebay.config import PASS_SHARE, REPEATS, WORKERS
from kettlebay.evals import score_conversation
from kettlebay.store import Store

OVERRIDES = Path("out/overrides.jsonl")
MIN_REASON_WORDS = 6


def run_release(release: Release, golden: list[dict], store_data: dict, client, repeats: int = REPEATS,
                workers: int = WORKERS) -> dict:
    """Every golden case, `repeats` times, each in a fresh copy of the store. Any exception is kept as
    that run's error, so a timeout shows up as a failed run instead of a missing one."""

    def one_run(case: dict) -> dict:
        try:
            return converse(case["request"], release, client, Store(store_data))
        except Exception as error:  # the model API, the network, a bad tool schema: all fail closed
            return {"answer": "", "calls": [], "usage": {"cost": 0.0}, "seconds": 0.0,
                    "error": f"{type(error).__name__}: {error}"}

    jobs = [case for case in golden for _ in range(repeats)]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one_run, jobs))
    cases: dict[str, list[dict]] = {}
    for case, result in zip(jobs, results):
        cases.setdefault(case["id"], []).append(result)
    return {"release": release.name, "fingerprint": release.fingerprint, "model": release.model, "repeats": repeats,
            "made_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seconds": round(time.monotonic() - started, 1),
            "cost": round(sum(result["usage"].get("cost", 0.0) for result in results), 6), "cases": cases}


def needed(runs: int) -> int:
    return math.ceil(runs * PASS_SHARE - 1e-9)


def case_outcome(case: dict, conversations: list[dict] | None, with_trajectory: bool) -> dict:
    """How one case did across its repeats. It passes when enough runs pass; one unsafe run fails it."""
    if not conversations:
        return {"passes": 0, "runs": 0, "passed": False, "missing": True, "errors": 0, "unsafe": False, "scores": []}
    scores = [score_conversation(case, conversation) for conversation in conversations]
    passes = sum(score["answer_ok"] and (score["trajectory_ok"] or not with_trajectory) for score in scores)
    # A write with no approval is not a wobble to average away: one is enough to fail the case.
    unsafe = with_trajectory and any(score["unsafe"] for score in scores)
    errors = sum(bool(conversation.get("error")) for conversation in conversations)
    return {"passes": passes, "runs": len(scores), "passed": passes >= needed(len(scores)) and not unsafe, "missing": False,
            "errors": errors, "unsafe": unsafe, "scores": scores}


@dataclass
class Verdict:
    release: str
    with_trajectory: bool
    rows: list[dict] = field(default_factory=list)

    def named(self, status: str) -> list[str]:
        return [row["id"] for row in self.rows if row["status"] == status]

    @property
    def regressed(self) -> list[str]:
        return self.named("regressed")

    @property
    def unsafe(self) -> list[str]:
        return self.named("unsafe")

    @property
    def broken(self) -> list[str]:
        """Cases nothing was measured on. These block, and no override can waive them."""
        return [row["id"] for row in self.rows if row["status"] in ("missing", "error")]

    @property
    def blocked(self) -> bool:
        return bool(self.regressed or self.unsafe or self.broken)


def verdict(golden: list[dict], live_run: dict, candidate_run: dict, with_trajectory: bool = True) -> Verdict:
    """A case regresses when it passes on the live release and fails on the candidate. A case already
    failing on live is reported, not blamed on the change, so the gate measures the change itself."""
    result = Verdict(candidate_run["release"], with_trajectory)
    for case in golden:
        live = case_outcome(case, live_run["cases"].get(case["id"]), with_trajectory)
        candidate = case_outcome(case, candidate_run["cases"].get(case["id"]), with_trajectory)
        if candidate["missing"] or live["missing"]:
            status = "missing"
        elif candidate["errors"] or live["errors"]:
            status = "error"
        elif candidate["unsafe"]:
            status = "unsafe"
        elif live["passed"] and not candidate["passed"]:
            status = "regressed"
        elif candidate["passed"] and not live["passed"]:
            status = "fixed"
        else:
            status = "held" if candidate["passed"] else "failing on live too"
        result.rows.append({"id": case["id"], "request": case["request"], "status": status,
                            "live": live, "candidate": candidate})
    return result


def summary_line(result: Verdict) -> str:
    if not result.blocked:
        return f"passed: no golden case is worse with {result.release}"
    parts = []
    if result.broken:
        parts.append(f"{len(result.broken)} cases with no result ({', '.join(result.broken)})")
    if result.unsafe:
        parts.append(f"{len(result.unsafe)} unsafe ({', '.join(result.unsafe)})")
    if result.regressed:
        parts.append(f"{len(result.regressed)} regressed ({', '.join(result.regressed)})")
    return f"blocked: {result.release} has " + ", ".join(parts)


def load_overrides(path: Path = OVERRIDES) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def find_override(fingerprint: str, path: Path = OVERRIDES) -> dict | None:
    """An override counts only for the release it was written for. Change one word of the prompt and
    the fingerprint changes, so the gate runs again and a person has to look again."""
    return next((entry for entry in reversed(load_overrides(path)) if entry["fingerprint"] == fingerprint), None)


def record_override(result: Verdict, fingerprint: str, by: str, reason: str, path: Path = OVERRIDES) -> dict:
    """A person ships a blocked release anyway. The reason is written down with the cases it waives."""
    if not result.blocked:
        raise ValueError("This release passed; there is nothing to override.")
    if result.broken:
        raise ValueError("Cases with no result cannot be overridden: nothing was measured. Run the check again.")
    if not by.strip():
        raise ValueError("Name the person who takes the decision.")
    if len(reason.split()) < MIN_REASON_WORDS:
        raise ValueError(f"Write the reason in at least {MIN_REASON_WORDS} words, so the next reader knows why.")
    entry = {"release": result.release, "fingerprint": fingerprint, "by": by.strip(), "reason": reason.strip(),
             "waived": result.regressed + result.unsafe, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as log:
        log.write(json.dumps(entry) + "\n")
    return entry
