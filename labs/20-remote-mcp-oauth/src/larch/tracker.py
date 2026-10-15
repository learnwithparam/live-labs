"""Larch, the issue tracker: Copperline Payments' workspace, its API, and its audit log.

This module is the product, and the source of truth. Its API trusts no caller by name. Every call
carries a bearer credential, and the API looks up whom that credential acts for and which scopes it
carries. The audit log records each call with the person, the client and the kind of credential, so
"who did this, and through what?" has an answer that no model wrote.
"""

import copy
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

WORKSPACE = Path("data/workspace.json")

# One scope per kind of action. The MCP tools, the token exchange and the review all read this table,
# so a tool can never ask for less than the API will check.
NEEDS = {
    "search_issues": "issues:read",
    "get_issue": "issues:read",
    "create_issue": "issues:write",
    "set_status": "issues:write",
    "assign_issue": "issues:write",
    "add_comment": "comments:write",
    "delete_issue": "issues:delete",
}

# The account an admin API key acts as. It belongs to no person, so it sees every private team.
SERVICE_ACCOUNT = {"id": "svc-mcp", "name": "Copperline MCP integration", "role": "admin"}


@dataclass(frozen=True)
class Credential:
    """Whom an API call acts for and what it may do.

    The authorization server mints one per token exchange, narrow and short-lived. An API key is
    one too, but it never expires and acts as a service account, not as the person asking.
    """

    actor: str
    scopes: frozenset[str]
    client: str
    via: str
    expires_at: float | None = None


class Refused(Exception):
    """The API said no. `status` is the HTTP code Larch's REST API would send: 401, 403 or 404."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


@dataclass
class Workspace:
    """One workspace's data, the credentials its API accepts, and everything done through it."""

    data: dict = field(default_factory=lambda: json.loads(WORKSPACE.read_text()))
    audit: list[dict] = field(default_factory=list)
    api_keys: dict[str, Credential] = field(default_factory=dict)
    # Other issuers the API trusts, such as the authorization server's token exchange.
    trusted: list[Callable[[str], Credential | None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.original = copy.deepcopy(self.data)

    # The workspace's settings page.

    def create_admin_api_key(self) -> str:
        """What a team does when it wires up an integration in a hurry: one key with every scope."""
        key = "lk_live_" + secrets.token_hex(16)
        everything = frozenset(self.data["roles"]["admin"])
        self.api_keys[key] = Credential(SERVICE_ACCOUNT["id"], everything, SERVICE_ACCOUNT["name"], "shared admin key")
        return key

    def user(self, user_id: str) -> dict | None:
        if user_id == SERVICE_ACCOUNT["id"]:
            return SERVICE_ACCOUNT
        return next((user for user in self.data["users"] if user["id"] == user_id), None)

    def role_scopes(self, user_id: str) -> list[str]:
        """The most a person could ever grant: a scope their own role does not hold is never theirs to give."""
        user = self.user(user_id)
        return self.data["roles"][user["role"]] if user else []

    def can_see(self, user_id: str, issue: dict) -> bool:
        """Public teams are open to everyone in the workspace; a private team only to its members."""
        if user_id == SERVICE_ACCOUNT["id"]:
            return True
        team = next(team for team in self.data["teams"] if team["key"] == issue["team"])
        return not team["private"] or user_id in team["members"]

    def name_of(self, user_id: str | None) -> str | None:
        user = self.user(user_id) if user_id else None
        return user["name"] if user else None

    # The API. Each call resolves its credential, checks the scope, the role and the team, then acts.

    def resolve(self, bearer: str) -> Credential:
        credential = self.api_keys.get(bearer) or next(
            (found for lookup in self.trusted if (found := lookup(bearer))), None)
        if credential is None or (credential.expires_at and credential.expires_at < time.time()):
            raise Refused(401, "The credential is unknown or expired.")
        return credential

    def call(self, bearer: str, action: str, **args) -> object:
        credential = self.resolve(bearer)
        needed = NEEDS[action]
        entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "actor": credential.actor,
                 "actor_name": self.name_of(credential.actor), "client": credential.client, "via": credential.via,
                 "action": action, "scope": needed, "issues": [], "args": args}
        try:
            if needed not in credential.scopes:
                raise Refused(403, f"This credential carries {', '.join(sorted(credential.scopes))}; {action} needs {needed}.")
            # A scope says what an app may do for a person, never more than the person may do.
            if needed not in self.role_scopes(credential.actor):
                raise Refused(403, f"{self.name_of(credential.actor)}'s role does not allow {needed}.")
            result = getattr(self, "_" + action)(credential.actor, entry, **args)
        except Refused as refusal:
            self.audit.append({**entry, "outcome": "refused", "detail": refusal.reason})
            raise
        self.audit.append({**entry, "outcome": "done", "detail": entry.get("detail", "")})
        return result

    def record_refusal(self, actor: str, client: str, via: str, action: str, reason: str, args: dict, issues: list[str]) -> None:
        """A refusal that happened before the API, at the token exchange, still belongs in the log."""
        self.audit.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "actor": actor,
                           "actor_name": self.name_of(actor), "client": client, "via": via, "action": action,
                           "scope": NEEDS[action], "issues": issues, "args": args, "outcome": "refused", "detail": reason})

    def changes(self) -> dict[str, object]:
        """Every field that differs from the seeded workspace, as `ISSUE.field: new value`."""
        before = {issue["id"]: issue for issue in self.original["issues"]}
        after = {issue["id"]: issue for issue in self.data["issues"]}
        found: dict[str, object] = {}
        for issue_id, issue in before.items():
            if issue_id not in after:
                found[f"{issue_id}"] = "deleted"
                continue
            for key, value in after[issue_id].items():
                if value != issue.get(key):
                    found[f"{issue_id}.{key}"] = value
        for issue_id in after.keys() - before.keys():
            found[issue_id] = "created"
        return found

    # The actions. Each finds issues only among those the actor can see, so a hidden issue reads as missing.

    def _visible(self, actor: str, issue_id: str) -> dict:
        issue = next((issue for issue in self.data["issues"] if issue["id"] == issue_id.upper()), None)
        # Larch answers 404, not 403, for a private issue: a 403 would confirm that the issue exists.
        if issue is None or not self.can_see(actor, issue):
            raise Refused(404, f"No issue {issue_id} that you can see.")
        return issue

    def _person(self, words: str, actor: str) -> str:
        if words.lower() in ("me", "myself"):
            return actor
        lowered = words.lower()
        match = next((user for user in self.data["users"]
                      if lowered in (user["id"], user["name"].lower(), user["name"].split()[0].lower())), None)
        if match is None:
            raise Refused(404, f"No person called {words!r} in this workspace.")
        return match["id"]

    def _shown(self, issue: dict) -> dict:
        return {**issue, "assignee": self.name_of(issue["assignee"]),
                "comments": [{"author": self.name_of(comment["author"]), "body": comment["body"]} for comment in issue["comments"]]}

    def _search_issues(self, actor: str, entry: dict, team: str | None = None, status: str | None = None,
                       assignee: str | None = None, text: str | None = None, include_done: bool = False) -> list[dict]:
        assignee_id = self._person(assignee, actor) if assignee else None
        found = [issue for issue in self.data["issues"] if self.can_see(actor, issue)
                 and (not team or issue["team"] == team.upper() or team.lower() in self._team_name(issue).lower())
                 and (not status or issue["status"].lower() == status.lower())
                 and (include_done or status or issue["status"] != "Done")
                 and (not assignee_id or issue["assignee"] == assignee_id)
                 and (not text or text.lower() in issue["title"].lower())]
        entry["issues"] = [issue["id"] for issue in found]
        return [{key: self._shown(issue)[key] for key in ("id", "title", "status", "assignee", "priority")} for issue in found]

    def _team_name(self, issue: dict) -> str:
        return next(team["name"] for team in self.data["teams"] if team["key"] == issue["team"])

    def _get_issue(self, actor: str, entry: dict, issue_id: str) -> dict:
        issue = self._visible(actor, issue_id)
        entry["issues"] = [issue["id"]]
        return self._shown(issue)

    def _create_issue(self, actor: str, entry: dict, team: str, title: str, priority: str = "Medium") -> dict:
        if not any(each["key"] == team.upper() for each in self.data["teams"]):
            raise Refused(404, f"No team {team}.")
        number = 1 + max(int(issue["id"].split("-")[1]) for issue in self.data["issues"] if issue["team"] == team.upper())
        issue = {"id": f"{team.upper()}-{number}", "team": team.upper(), "title": title, "status": "Todo",
                 "assignee": None, "priority": priority, "comments": []}
        if not self.can_see(actor, issue):
            raise Refused(404, f"No team {team} that you can see.")
        self.data["issues"].append(issue)
        entry["issues"] = [issue["id"]]
        return self._shown(issue)

    def _set_status(self, actor: str, entry: dict, issue_id: str, status: str) -> dict:
        issue = self._visible(actor, issue_id)
        chosen = next((each for each in self.data["statuses"] if each.lower() == status.lower()), None)
        if chosen is None:
            raise Refused(400, f"Status must be one of {', '.join(self.data['statuses'])}.")
        entry["issues"], entry["detail"] = [issue["id"]], f"{issue['status']} to {chosen}"
        issue["status"] = chosen
        return self._shown(issue)

    def _assign_issue(self, actor: str, entry: dict, issue_id: str, assignee: str) -> dict:
        issue = self._visible(actor, issue_id)
        issue["assignee"] = self._person(assignee, actor)
        entry["issues"], entry["detail"] = [issue["id"]], f"to {self.name_of(issue['assignee'])}"
        return self._shown(issue)

    def _add_comment(self, actor: str, entry: dict, issue_id: str, body: str) -> dict:
        issue = self._visible(actor, issue_id)
        # The comment is signed by whoever the credential acts for. With a shared key, that is the bot.
        issue["comments"].append({"author": actor, "body": body})
        entry["issues"], entry["detail"] = [issue["id"]], body[:80]
        return self._shown(issue)

    def _delete_issue(self, actor: str, entry: dict, issue_id: str) -> dict:
        issue = self._visible(actor, issue_id)
        self.data["issues"].remove(issue)
        entry["issues"], entry["detail"] = [issue["id"]], issue["title"]
        return {"deleted": issue["id"]}
