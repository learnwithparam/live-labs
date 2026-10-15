"""The AI inventory: every system allowed to call a model, kept as code and checked by a schema.

An inventory in a spreadsheet drifts from what runs. Kept as a reviewed TOML file that the gateway reads
on every call, it cannot drift: a system that is not listed cannot reach the model at all.
"""

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

from larkspur.config import DATA

ARTICLE_50 = "EU AI Act Article 50(1): people must be told they are interacting with an AI system"


@dataclass(frozen=True)
class PromptVersion:
    version: str
    starts: str  # an ISO date; the version answers every call on or after it, until the next one starts
    prompt_path: str
    instructions: str


@dataclass(frozen=True)
class System:
    id: str
    name: str
    purpose: str
    owner: str
    talks_to_customers: bool
    disclosure: str
    proposed_disclosure: str
    risk: str
    status: str
    models: list[str]
    versions: list[PromptVersion] = field(default_factory=list)

    def version_at(self, timestamp: str) -> PromptVersion | None:
        """The prompt version in effect at a call's time. The log records it, so a reply is never orphaned."""
        in_effect = [version for version in self.versions if version.starts <= timestamp[:10]]
        return max(in_effect, key=lambda version: version.starts) if in_effect else None

    def card(self) -> dict:
        return {"id": self.id, "name": self.name, "purpose": self.purpose, "owner": self.owner,
                "talks_to_customers": self.talks_to_customers, "disclosure": self.disclosure,
                "proposed_disclosure": self.proposed_disclosure, "risk": self.risk, "status": self.status,
                "models": self.models, "versions": [{"version": version.version, "from": version.starts,
                                                     "prompt": version.prompt_path} for version in self.versions]}


class InventoryError(ValueError):
    """The inventory file is malformed. Nothing runs on a malformed inventory, so CI stops the change."""


def schema_errors(document: dict, schema_path: Path) -> list[str]:
    """Every schema violation, with where it is, so a reviewer can fix the pull request in one pass."""
    validator = Draft202012Validator(json.loads(schema_path.read_text()))
    return [f"{'/'.join(str(part) for part in error.absolute_path) or 'inventory'}: {error.message}"
            for error in sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))]


def load(data_dir: Path = DATA) -> dict[str, System]:
    """Read, validate and index the inventory. The schema checks shape; this checks what a schema cannot."""
    document = tomllib.loads((data_dir / "inventory.toml").read_text())
    problems = schema_errors(document, data_dir / "inventory.schema.json")
    systems: dict[str, System] = {}
    for entry in document.get("system", []) if not problems else []:
        if entry["id"] in systems:
            problems.append(f"{entry['id']}: listed twice")
        versions = []
        for version in entry["version"]:
            prompt_file = data_dir / version["prompt"]
            if not prompt_file.exists():
                problems.append(f"{entry['id']}: prompt file {version['prompt']} does not exist")
                continue
            versions.append(PromptVersion(version["version"], version["from"], version["prompt"],
                                          prompt_file.read_text().strip()))
        systems[entry["id"]] = System(
            id=entry["id"], name=entry["name"], purpose=entry["purpose"], owner=entry["owner"],
            talks_to_customers=entry["talks_to_customers"], disclosure=entry.get("disclosure", "").strip(),
            proposed_disclosure=entry.get("proposed_disclosure", "").strip(), risk=entry["risk"],
            status=entry["status"], models=entry["models"], versions=versions)
    if problems:
        raise InventoryError("; ".join(problems))
    return systems


def blockers(system: System) -> list[str]:
    """Why a listed system may not ship. A customer-facing system with no disclosure is the one that bites."""
    found = []
    if system.status != "live":
        found.append(f"{system.id} is {system.status}")
    # The rule is checked at call time too, so a missing disclosure cannot reach a customer by any path.
    if system.talks_to_customers and not system.disclosure:
        found.append(f"{system.id} talks to customers and has no AI disclosure ({ARTICLE_50})")
    return found


def findings(systems: dict[str, System]) -> list[dict]:
    """The cases that go to the compliance officer: each blocked system, its reason, and what the owner proposes."""
    return [{"system": system.id, "name": system.name, "owner": system.owner, "reason": reason,
             "proposed_disclosure": system.proposed_disclosure}
            for system in systems.values() for reason in blockers(system)]
