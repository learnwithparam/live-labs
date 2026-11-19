"""The signed benchmark and latency budget: the truth the RFC is checked against.

A person signed both before any code existed. `signoff.toml` records a hash of each file, so a case
added or removed afterwards, to make a number look better, shows up as a changed file.
"""

import hashlib
import json
import tomllib
from collections import Counter
from pathlib import Path

from brickhaven_rfc.config import DATA


def load_cases(directory: Path = DATA) -> list[dict]:
    lines = (directory / "benchmark.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def load_signoff(directory: Path = DATA) -> dict:
    return tomllib.loads((directory / "signoff.toml").read_text())


def signed_budget_ms(directory: Path = DATA) -> int:
    return tomllib.loads((directory / "latency_budget.toml").read_text())["p95_ms"]


def changed_files(directory: Path = DATA) -> list[str]:
    """Names of signed files whose bytes no longer match the hash recorded at sign-off."""
    recorded = load_signoff(directory)["sha256"]
    return [name for name, digest in recorded.items() if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest]


def cases_per_tag(cases: list[dict]) -> Counter:
    return Counter(tag for case in cases for tag in case["tags"])
