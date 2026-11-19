"""The golden dataset: cases a person verified, and the bench where a person verifies more.

The model never writes the truth. A case counts only when a named person verified it on a date, so a
case the model proposed and nobody checked cannot move a score.
"""

import json
from pathlib import Path

from brickhaven_evals.config import DATA, OUT

VERIFIED_BY_LABELLER = OUT / "verified.jsonl"


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def is_verified(case: dict) -> bool:
    # A blank name or date means "proposed, not checked": it is kept on disk but never scored.
    return bool(str(case.get("verified_by", "")).strip() and str(case.get("verified_on", "")).strip())


def load_golden(golden_path: Path | None = None, labelled_path: Path | None = None) -> list[dict]:
    """Verified cases only: the signed file first, then whatever the labeller verified on the bench."""
    rows = read_rows(golden_path or DATA / "golden.jsonl") + read_rows(labelled_path or VERIFIED_BY_LABELLER)
    return [case for case in rows if is_verified(case)]


def waiting_for_a_person(unlabelled_path: Path | None = None, labelled_path: Path | None = None) -> list[dict]:
    """Cases the model proposed that no person has verified yet."""
    done = {case["id"] for case in read_rows(labelled_path or VERIFIED_BY_LABELLER)}
    return [case for case in read_rows(unlabelled_path or DATA / "unlabelled.jsonl") if case["id"] not in done]


def verify_case(case_id: str, labeller: str, verified_on: str, corrected: dict | None = None,
                unlabelled_path: Path | None = None, labelled_path: Path | None = None) -> dict:
    """A person accepts the proposal as it is, or replaces it with their own work orders."""
    if not labeller.strip():
        raise ValueError("A verified case needs the name of the person who verified it.")
    proposals = {case["id"]: case for case in read_rows(unlabelled_path or DATA / "unlabelled.jsonl")}
    if case_id not in proposals:
        raise KeyError(case_id)
    proposal = proposals[case_id]
    expected = corrected if corrected is not None else proposal["proposed"]
    verified = {"id": case_id, "tag": proposal["tag"], "message": proposal["message"], "expected": expected,
                "verified_by": labeller.strip(), "verified_on": verified_on,
                "corrected": corrected is not None}
    target = labelled_path or VERIFIED_BY_LABELLER
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a") as handle:
        handle.write(json.dumps(verified, ensure_ascii=False) + "\n")
    return verified
