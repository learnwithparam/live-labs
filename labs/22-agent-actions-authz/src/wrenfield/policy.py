"""Ask the policy before every tool call: allow, ask a person, or deny, with the rule that decided.

RBAC answers "may this role use this tool"; ABAC adds "under these conditions": is the branch
protected, is the database prod, is the file under migrations/, how big is the edit. The attributes
come from the repo's settings and the workspace, never from the model, so a prompt cannot change them.
"""

import csv
import posixpath
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from cerbos.sdk.model import Principal, Resource, ResourceList

from wrenfield.tools import destructive, read_only
from wrenfield.workspace import DATA, Workspace, is_protected

ROLES = ["intern", "engineer", "release_manager"]
TABLE = DATA / "policy_table.csv"


@dataclass
class Decision:
    verdict: str  # allow, ask or deny
    reason: str
    rule: str
    attributes: dict
    # What the real policy says. The baseline runs every call but still records this, the way a team
    # runs a new policy in shadow mode to see what it would have stopped before switching it on.
    policy_verdict: str = ""

    def __post_init__(self):
        self.policy_verdict = self.policy_verdict or self.verdict


def call_attributes(tool: str, args: dict, lines_changed: int = 0) -> dict:
    """Every fact the policy may test, for one call. Each key is always present, so a rule never errors."""
    return {
        "tool": tool,
        "read_only": read_only(tool),
        "destructive": destructive(tool),
        "branch_protected": is_protected(args.get("branch", "")) if args.get("branch") else False,
        "environment": args.get("environment", ""),
        # Normalised and case-folded, so "payroll/../MIGRATIONS/x.sql" is under migrations/ as the disk sees it.
        "under_migrations": Path(posixpath.normpath(args.get("path", "") or ".").casefold()).parts[:1] == ("migrations",),
        "lines_changed": lines_changed,
    }


class Policy:
    """Wrenfield's rules, enforced by Cerbos. The agent loop calls `decide` and obeys the verdict."""

    enforced = True

    def __init__(self, client):
        self.client = client

    def decide(self, person: dict, tool: str, args: dict, workspace: Workspace | None = None) -> Decision:
        lines = workspace.lines_changed(args.get("path", ""), args.get("content", "")) if workspace and tool == "write_file" else 0
        return self.check(person, call_attributes(tool, args, lines))

    def check(self, person: dict, attributes: dict) -> Decision:
        # Two actions in one request: may the call happen at all, and may it happen with no person.
        principal = Principal(person["id"], roles={person["role"]})
        resource = Resource(uuid.uuid4().hex[:12], "tool", attr=attributes)
        response = self.client.check_resources(principal, ResourceList().add(resource, {"run", "run_unattended"}))
        result = response.raise_if_failed().results[0]
        verdict = "allow" if result.is_allowed("run_unattended") else "ask" if result.is_allowed("run") else "deny"
        # Each rule's name starts with what it decides, so the rule that explains this verdict is the one
        # on the deciding action whose name carries the verdict. A call no rule allows is denied by default.
        deciding = "run_unattended" if verdict == "allow" else "run"
        for output in result.outputs or []:
            rule = output.src.rsplit("#", 1)[-1]
            if output.action == deciding and rule.startswith(verdict):
                return Decision(verdict, str(output.val), rule, attributes)
        return Decision(verdict, f"No rule lets a {person['role'].replace('_', ' ')} run {attributes['tool']}",
                        "default-deny", attributes)


class PromptOnly:
    """The naive baseline: the rule lives in the system prompt, and every call the model makes runs."""

    enforced = False

    def __init__(self, shadow: Policy):
        self.shadow = shadow

    def decide(self, person: dict, tool: str, args: dict, workspace: Workspace | None = None) -> Decision:
        would = self.shadow.decide(person, tool, args, workspace)
        return Decision("allow", "No policy: the prompt is the only rule", "prompt-only", would.attributes,
                        policy_verdict=would.verdict)


def table() -> list[dict]:
    """The truth the lab is built on: every situation, and what each role should get."""
    with TABLE.open() as handle:
        return list(csv.DictReader(handle))


def row_args(row: dict) -> dict:
    return {key: row[key] for key in ("branch", "environment", "path") if row[key]}


def check_table(policy: Policy) -> list[dict]:
    """Ask the live PDP about every cell of the table; a cell that disagrees is a broken rule."""
    cells = []
    for row in table():
        attributes = call_attributes(row["tool"], row_args(row), int(row["lines_changed"] or 0))
        for role in ROLES:
            decision = policy.check({"id": f"table-{role}", "role": role}, attributes)
            cells.append({"case": row["case"], "role": role, "expected": row[role], **asdict(decision),
                          "ok": decision.verdict == row[role]})
    return cells
