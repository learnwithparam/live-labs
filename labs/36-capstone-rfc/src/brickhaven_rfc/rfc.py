"""Read an RFC: the structured `rfc.toml`, or the prose draft a team usually starts with.

The prose draft is read the way a reviewer reads it. Each bullet under "Risks" is a failure mode and
each "name: about N s" line is a step's time. Whatever the draft does not say stays empty, and an empty
metric, threshold or tag is exactly what the review calls unmeasured.
"""

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

STEP_LINE = re.compile(r"^- (.+?): about ([\d.]+) s\s*$")


@dataclass
class FailureMode:
    id: str
    what: str
    scored_field: str = ""
    metric: str = ""
    threshold: float | None = None
    tag: str = ""


@dataclass
class Rfc:
    title: str
    budget_ms: dict[str, int]
    failure_modes: list[FailureMode]
    eval_plan: dict[str, float]
    digest: str  # of the file's bytes, so a reviewer's note is tied to this exact version


def read_draft(path: Path) -> Rfc:
    title, section, budget_ms, modes = "", "", {}, []
    for line in path.read_text().splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            section = line[3:].strip().lower()
        elif section == "time" and (step := STEP_LINE.match(line)):
            # The draft talks in seconds; the signed budget is in milliseconds.
            budget_ms[step.group(1)] = round(float(step.group(2)) * 1000)
        elif section == "risks" and line.startswith("- "):
            modes.append(FailureMode(id=f"risk-{len(modes) + 1}", what=line[2:].strip()))
    return Rfc(title, budget_ms, modes, {}, hashlib.sha256(path.read_bytes()).hexdigest())


def read_structured(path: Path) -> Rfc:
    document = tomllib.loads(path.read_text())
    modes = [FailureMode(id=row["id"], what=row["what"], scored_field=row.get("field", ""), metric=row.get("metric", ""),
                         threshold=row.get("threshold"), tag=row.get("tag", "")) for row in document.get("failure_mode", [])]
    return Rfc(document["title"], document.get("budget_ms", {}), modes, document.get("eval_plan", {}),
               hashlib.sha256(path.read_bytes()).hexdigest())


def read_rfc(path: Path) -> Rfc:
    return read_structured(path) if path.suffix == ".toml" else read_draft(path)
