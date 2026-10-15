"""Mosswood's project data and the two read-only tools the assistant may call on it.

The tenant is never an argument the model can fill in. It comes from the verified token, through the
gateway, so a question asked in one company's workspace can only ever read that company's projects.
"""

import json
from datetime import date
from functools import cache

from mosswood.config import DATA


@cache
def workspaces() -> dict:
    return json.loads(DATA.read_text())


def today() -> date:
    # The data is a snapshot, so "overdue" is measured against the day it was taken, not the clock.
    return date.fromisoformat(workspaces()["today"])


def tenant(tenant_id: str) -> dict:
    return workspaces()["tenants"][tenant_id]


def users() -> list[tuple[str, str]]:
    """Every (tenant, user) pair: who the web app can sign in, and who the load test plays."""
    return [(tenant_id, user_id) for tenant_id, info in workspaces()["tenants"].items() for user_id in info["users"]]


def tenant_limit(tenant_id: str) -> int:
    """A tenant's requests per window, from its plan. An operator's raise is applied on top in `gateway`."""
    plan = workspaces()["plans"][tenant(tenant_id)["plan"]]
    return plan["requests_per_window"]


def tool(name: str, description: str, **properties: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": [], "additionalProperties": False}}}


# Both tools only read. An assistant inside a customer's app answers questions; changing a task is a
# different job with its own approval step, so no tool here can write.
TOOLS = [
    tool("list_projects", "List the projects in this workspace with their owner, due date and how many tasks are open."),
    tool("search_tasks", "Find tasks in this workspace. Every filter is optional.",
         project={"type": "string", "description": "A project id like PF-Q4, or empty for all projects."},
         status={"type": "string", "enum": ["", "todo", "in_progress", "blocked", "done"]},
         owner={"type": "string", "description": "A user id like priya.raman, or empty for anyone."},
         overdue_only={"type": "boolean", "description": "Only tasks past their due date that are not done."}),
]


def list_projects(tenant_id: str) -> list[dict]:
    info = tenant(tenant_id)
    return [{**project, "open_tasks": sum(1 for task in info["tasks"] if task["project"] == project["id"] and task["status"] != "done")}
            for project in info["projects"]]


def search_tasks(tenant_id: str, project: str = "", status: str = "", owner: str = "", overdue_only: bool = False) -> list[dict]:
    found = []
    for task in tenant(tenant_id)["tasks"]:
        overdue = task["status"] != "done" and date.fromisoformat(task["due"]) < today()
        if (project and task["project"] != project) or (status and task["status"] != status) \
                or (owner and task["owner"] != owner) or (overdue_only and not overdue):
            continue
        found.append({**task, "overdue": overdue})
    return found


def call_tool(tenant_id: str, name: str, arguments: dict) -> str:
    """Run one tool for one tenant and return JSON text for the model. A bad call gets a reason back."""
    if name == "list_projects":
        return json.dumps(list_projects(tenant_id))
    if name == "search_tasks":
        allowed = {key: value for key, value in arguments.items() if key in ("project", "status", "owner", "overdue_only")}
        return json.dumps(search_tasks(tenant_id, **allowed))
    return json.dumps({"error": f"No tool {name!r}. Use list_projects or search_tasks."})
