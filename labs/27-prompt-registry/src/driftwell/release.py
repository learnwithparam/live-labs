"""Releasing a prompt: a person promotes a version by moving the `production` label, behind a score gate.

The gate fails closed. A version with no golden set score, or one below the bar, cannot take production
unless a named person overrides it with a reason. Rollback skips the gate on purpose: it returns to a
version that already served customers, and speed matters more than a second review.
"""

import json
import math
import time
from pathlib import Path

from driftwell.config import PASS_BAR
from driftwell.golden import fingerprint
from driftwell.registry import holder, move_label, versions

SCORES = Path("out/scores.json")
RELEASES = Path("out/releases.json")


class Refused(Exception):
    """The gate said no. The message says why, in words a product manager can act on."""


class Scores:
    """Golden set results per prompt text, saved to disk so the page and the gate read the same numbers."""

    def __init__(self, path: Path = SCORES):
        self.path = path
        self.records = json.loads(path.read_text()) if path.exists() else {}

    def of(self, text: str) -> dict | None:
        return self.records.get(fingerprint(text))

    def put(self, text: str, version: int, record: dict) -> None:
        self.records[fingerprint(text)] = {**record, "version": version}
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(self.records, indent=1))

    def clear(self) -> None:
        self.records = {}
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text("{}")


class ReleaseLog:
    """Every label move with who made it and why. Langfuse keeps the labels, not their history."""

    def __init__(self, path: Path = RELEASES):
        self.path = path
        self.events: list[dict] = json.loads(path.read_text()) if path.exists() else []

    def add(self, **event) -> dict:
        self.events.append(event)
        self.save()
        return event

    def save(self) -> None:
        self.path.parent.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(self.events, indent=1))

    def clear(self) -> None:
        self.events = []
        self.save()


def needed(total: int, bar: float = PASS_BAR) -> int:
    """How many golden conversations must pass: the bar as a count, rounded up so the bar is never lowered."""
    return math.ceil(round(bar * total, 6))


def gate(record: dict | None, bar: float = PASS_BAR) -> str | None:
    """Why a version may not take production, or None when it may."""
    if record is None:
        return "it has not been scored on the golden set"
    if record["score"] < bar:
        return f"it passes {record['passed']} of {record['total']} golden conversations, below the bar of {needed(record['total'], bar)}"
    return None


def promote(client, log: ReleaseLog, scores: Scores, number: int, by: str, override: str = "",
            bar: float = PASS_BAR, now=time.time) -> dict:
    """Move production to version `number`, signed by a person. Below the bar, only an override moves it."""
    if not by.strip():
        raise Refused("a promotion needs the name of the person who decided it")
    all_versions = versions(client)
    target = next((version for version in all_versions if version.number == number), None)
    if target is None:
        raise Refused(f"there is no version {number}")
    current = holder(all_versions, "production")
    record = scores.of(target.text)
    blocked = gate(record, bar)
    if blocked and not override.strip():
        raise Refused(f"Version {number} cannot go to production: {blocked}. A person can override with a reason.")
    move_label(client, number, "production")
    return log.add(at=now(), kind="override" if blocked else "promote", label="production",
                   came_from=current.number if current else None, to=number, by=by,
                   score=record["score"] if record else None, reason=override.strip() or None, served_after=None)


def rollback_target(log: ReleaseLog, current: int | None) -> int | None:
    """The version production held before `current` was promoted, read from the release log.

    Rollback events are skipped: rolling back twice must not undo the first rollback and re-ship the bad version.
    """
    last = next((event for event in reversed(log.events)
                 if event["label"] == "production" and event["kind"] != "rollback" and current is not None
                 and event["to"] == current and event["came_from"] is not None), None)
    return last["came_from"] if last else None


def rollback(client, log: ReleaseLog, by: str, now=time.time) -> dict:
    """Put production back on the version it held before the last promotion. One API call, no deploy."""
    if not by.strip():
        raise Refused("a rollback needs the name of the person who decided it")
    current = holder(versions(client), "production")
    target = rollback_target(log, current.number if current else None)
    if target is None:
        raise Refused("there is no earlier production version in the release log to go back to")
    move_label(client, target, "production")
    return log.add(at=now(), kind="rollback", label="production", came_from=current.number, to=target,
                   by=by, score=None, reason=f"Rolled back from version {current.number}", served_after=None)


def note_reply(log: ReleaseLog, reply: dict) -> dict | None:
    """The metric: the first reply after a rollback that came from the restored version closes the clock."""
    for event in reversed(log.events):
        if event["kind"] != "rollback":
            continue
        if event["served_after"] is None and reply["version"] == event["to"] and reply["at"] >= event["at"]:
            event["served_after"] = round(reply["at"] - event["at"], 1)
            log.save()
            return event
        return None
    return None
