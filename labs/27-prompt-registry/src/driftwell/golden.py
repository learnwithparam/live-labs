"""The golden set: 16 real-looking support conversations, each with checks a person wrote from policy.

The checks are the truth, not the model. A version's score is the share of conversations whose reply
passes every check, and that score decides whether the version may hold the production label.
"""

import hashlib
import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[2] / "data" / "golden.jsonl"


def cases(path: Path = GOLDEN) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def failed_checks(case: dict, reply: str) -> list[str]:
    """What the reply got wrong, in the words of the check. An empty list means it passed."""
    failed = []
    # Models write "don’t" with a curly apostrophe. Without this, a correct reply fails "don't share".
    reply = reply.replace("’", "'")
    for check in case["checks"]:
        if "must" in check and not re.search(check["must"], reply, re.I):
            failed.append(f"does not say {check['says']}")
        if "must_not" in check and re.search(check["must_not"], reply, re.I):
            # A must_not check names what the reply must not do, so its failure reads as the opposite.
            failed.append(f"breaks: {check['says']}")
    return failed


def fingerprint(text: str) -> str:
    """Scores are keyed by the prompt's text, so a re-created version with the same words keeps its score."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def score(all_cases: list[dict], answer: Callable[[dict], dict], workers: int = 4) -> dict:
    """Run every conversation through `answer` and grade each reply. Four at a time keeps a run under a minute."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        answers = list(pool.map(answer, all_cases))
    rows = []
    for case, answered in zip(all_cases, answers):
        failed = failed_checks(case, answered["reply"])
        rows.append({"id": case["id"], "topic": case["topic"], "message": case["message"], "reply": answered["reply"],
                     "failed": failed, "passed": not failed, "cost": answered["usage"]["cost"]})
    passed = sum(row["passed"] for row in rows)
    return {"passed": passed, "total": len(rows), "score": round(passed / len(rows), 3) if rows else 0.0,
            "cost": round(sum(row["cost"] for row in rows), 6), "rows": rows}
