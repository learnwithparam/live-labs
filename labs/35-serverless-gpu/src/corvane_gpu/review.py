"""The review queue's answers: a person picks the category for each item the served model did not settle.

An item lands in the queue when its answer failed validation or never arrived. The reviewer's
decision is stored beside the run, never written back into it, so the measured run stays as it was.
"""

import json
import time
from pathlib import Path

from corvane_gpu.golden import CATEGORIES


def decisions(path: Path) -> dict[str, dict]:
    """The latest decision per item. A reviewer can change their mind; the last answer stands."""
    if not path.exists():
        return {}
    latest = {}
    for line in path.read_text().splitlines():
        if line.strip():
            decision = json.loads(line)
            latest[decision["id"]] = decision
    return latest


def decide(path: Path, queue_ids: set[str], item_id: str, category: str, reviewer: str) -> dict:
    """Record one decision. Only queued items and real categories are accepted, and a name is required,
    because an answer nobody owns is not a review."""
    if item_id not in queue_ids:
        raise ValueError(f"{item_id} is not in the review queue")
    if category not in CATEGORIES:
        raise ValueError(f"{category} is not a category")
    if not reviewer.strip():
        raise ValueError("say who is deciding")
    decision = {"id": item_id, "category": category, "reviewer": reviewer.strip()[:60],
                "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as log:
        log.write(json.dumps(decision) + "\n")
    return decision
