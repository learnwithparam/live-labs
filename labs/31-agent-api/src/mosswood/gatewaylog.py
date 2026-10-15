"""Read the metric from the gateway's access log, the one record that every request passes through.

The agent's own numbers miss what never reached it: a 401, a 429, a connection the gateway cut. The
load tester's numbers include its own laptop's delays. Envoy logs each request once, with the time its
first byte came back from the agent, so the metric is read here.
"""

import json
from pathlib import Path

from mosswood.config import USER_REQUESTS_PER_WINDOW, WINDOW_SECONDS
from mosswood.gateway import ACCESS_LOG
from mosswood.workspace import tenant, workspaces

# Envoy's response flags that mean the request failed on the way: upstream timeout, stream idle timeout,
# upstream connection failure or reset, no healthy upstream. "DC" is the caller hanging up, not an error.
ERROR_FLAGS = {"UT", "SI", "UF", "UC", "UH", "URX", "UO", "UR", "DT"}


def rows(path: Path = ACCESS_LOG, since: float = 0.0, until: float = float("inf")) -> list[dict]:
    """The logged requests between two times. Browser preflights are not requests for an answer."""
    if not path.exists():
        return []
    found = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("method") == "OPTIONS" or not since <= float(row["at"]) <= until:
            continue
        found.append(row)
    return found


def percentile(values: list[float], share: float) -> float | None:
    """Nearest-rank percentile: the value that `share` of the requests were at or under."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-round(share * 100) * len(ordered) // 100))
    return ordered[rank - 1]


def failed(row: dict) -> bool:
    flags = set(str(row.get("flags") or "-").split(","))
    status = row.get("status") or 0
    return status >= 500 or bool(flags & ERROR_FLAGS) or (status == 0 and "DC" not in flags)


def tally(group: list[dict]) -> dict:
    admitted = [row for row in group if row.get("upstream")]
    answered = [row for row in admitted if row.get("status") == 200 and not failed(row)]
    errors = sum(failed(row) for row in admitted)
    return {
        "requests": len(group),
        "answered": len(answered),
        "refused_user": sum(row.get("status") == 429 and row.get("limit_scope") == "user" for row in group),
        "refused_tenant": sum(row.get("status") == 429 and row.get("limit_scope") == "tenant" for row in group),
        "unauthorized": sum(row.get("status") == 401 for row in group),
        "errors": errors,
        # Errors are counted against what the gateway let through: a 429 or a 401 is the gateway working.
        "error_rate": round(errors / len(admitted), 4) if admitted else 0.0,
        "p95_first_token_ms": percentile([row["first_byte_ms"] for row in answered if row.get("first_byte_ms") is not None], 0.95),
        "p50_first_token_ms": percentile([row["first_byte_ms"] for row in answered if row.get("first_byte_ms") is not None], 0.50),
    }


def summarise(logged: list[dict], limits: dict[str, int]) -> dict:
    """The operator's numbers: the whole gateway, each tenant, each user, and what needs a person."""
    by_tenant = {tenant_id: tally([row for row in logged if row.get("tenant") == tenant_id]) for tenant_id in workspaces()["tenants"]}
    users = sorted({(row["tenant"], row["user"]) for row in logged if row.get("tenant") and row.get("user")})
    by_user = {f"{tenant_id}/{user_id}": tally([row for row in logged if row.get("user") == user_id and row.get("tenant") == tenant_id])
               for tenant_id, user_id in users}
    return {
        "overall": tally(logged),
        "tenants": by_tenant,
        "users": by_user,
        # A 401 must never reach the agent. The gateway answered it, so no upstream host is logged.
        "unauthorized_reached_agent": sum(row.get("status") == 401 and bool(row.get("upstream")) for row in logged),
        "needs_operator": needs_operator(by_tenant, by_user, limits),
    }


def needs_operator(by_tenant: dict, by_user: dict, limits: dict[str, int]) -> list[dict]:
    """A tenant refused at its own workspace limit is a decision for a person; a user at theirs is not.

    One script over its user limit is the limit doing its job, and its colleagues are unaffected. A
    whole team over the workspace limit may be a customer who has outgrown its plan, which is a sales
    and capacity call the gateway cannot make.
    """
    cases = []
    for tenant_id, numbers in by_tenant.items():
        if not numbers["refused_tenant"]:
            continue
        people = {key.split("/", 1)[1]: user for key, user in by_user.items() if key.startswith(f"{tenant_id}/")}
        hit = sorted(user_id for user_id, user in people.items() if user["refused_tenant"])
        over_own = sorted(user_id for user_id, user in people.items() if user["refused_user"])
        info = tenant(tenant_id)
        plan = workspaces()["plans"][info["plan"]]
        cases.append({
            "tenant": tenant_id, "name": info["name"], "plan": plan["name"], "limit": limits[tenant_id],
            "refused": numbers["refused_tenant"], "people": hit, "over_own_limit": over_own,
            "reason": (f"{numbers['refused_tenant']} requests refused at the workspace limit of {limits[tenant_id]} per "
                       f"{WINDOW_SECONDS} s, across {len(hit)} people. "
                       + ("None of them was over their own limit of " f"{USER_REQUESTS_PER_WINDOW}."
                          if not over_own else f"{', '.join(over_own)} also hit their own limit.")),
        })
    return cases
