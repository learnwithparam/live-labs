"""The pin check: the one rule agentgateway has no switch for, plugged in as its guardrail processor.

agentgateway sends each MCP request (and each tool list on its way back) to this gRPC service using
its `ExtMcp` protocol, and waits for pass, refuse or a rewritten body. Three things make it safe:

- It reads the approved list on every check. If the list is missing or broken it refuses, so a
  bad deploy of the list blocks everything instead of allowing everything.
- On `tools/call` it asks the upstream server for the tool's definition *now* and compares its hash
  to the pin. Checking only at list time would miss a server that changes between list and call.
- The gateway is configured `failClosed`, so if this service is down every call is refused.

A changed or unpinned tool also disappears from the tool list, so the model never reads its
description. A drift alert is written for the platform team to decide on.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# gRPC prints a warning each time the process starts agentgateway (a fork); it is harmless here.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "false")
import grpc  # noqa: E402

from basalt.catalogue import CatalogueUnreadable, ApprovedServer, compare, live_definitions, load
from basalt.config import CATALOGUE, GUARD_PORT, HOST, OUT

# The gateway's protocol, read from the .proto file agentgateway publishes, so no generated code is kept.
protos, services = grpc.protos_and_services("basalt/ext_mcp.proto")
DRIFT = OUT / "drift.json"


def passed():
    return protos.McpRequestResult(**{"pass": protos.Pass()})


def refused(reason: str):
    return protos.McpRequestResult(error=protos.AuthorizationError(code=protos.AuthorizationError.PERMISSION_DENIED,
                                                                    reason=reason))


class DriftAlerts:
    """Tools whose live definition no longer matches the pin, kept until a reviewer decides."""

    def __init__(self, path: Path | None = None):
        self.path = path or DRIFT

    def read(self) -> list[dict]:
        return json.loads(self.path.read_text()) if self.path.exists() else []

    def raise_for(self, row: dict) -> None:
        alerts = self.read()
        if any(alert["server"] == row["server"] and alert["tool"] == row["tool"] and alert["current"] == row["current"]
               for alert in alerts):
            return
        seen = datetime.now(timezone.utc).isoformat(timespec="seconds")
        alerts.append({**row, "seen": seen, "decision": None})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(alerts, indent=1))


class PinGuard(services.ExtMcpServicer):
    def __init__(self, catalogue: Path = CATALOGUE, alerts: DriftAlerts | None = None):
        self.catalogue, self.alerts = catalogue, alerts or DriftAlerts()

    async def CheckRequest(self, request, context):
        try:
            servers = load(self.catalogue)
        except CatalogueUnreadable:
            return refused("approved server list unreadable, so every call is refused")
        if request.method != "tools/call":
            return passed()
        server_name = request.service_names[0] if request.service_names else ""
        tool_name = json.loads(request.mcp_request or b"{}").get("name", "")
        return refused(reason) if (reason := await self.call_problem(servers, server_name, tool_name)) else passed()

    async def call_problem(self, servers: dict[str, ApprovedServer], server_name: str, tool_name: str) -> str:
        """Why this call must not run, or "" when the server is listed and the tool still matches its pin."""
        server = servers.get(server_name)
        if not server:
            return f"{server_name} is not on the approved server list"
        if tool_name not in server.tools:
            return f"{server_name}.{tool_name} is not pinned on the approved list"
        try:
            live = await live_definitions(server.url)
        except Exception as error:  # an unreachable server cannot prove its tool is unchanged
            return f"could not read {server_name}'s tools to check the pin ({type(error).__name__})"
        for row in compare(server, live):
            if row["tool"] != tool_name:
                continue
            if row["state"] == "pinned":
                return ""
            self.alerts.raise_for(row)
            return f"pinned hash mismatch: {server_name}.{tool_name} changed since it was approved"
        return f"{server_name} no longer offers {tool_name}"

    async def CheckResponse(self, request, context):
        if request.method != "tools/list":
            return protos.McpResponseResult(**{"pass": protos.Pass()})
        try:
            servers = load(self.catalogue)
        except CatalogueUnreadable:
            return protos.McpResponseResult(error=protos.AuthorizationError(
                code=protos.AuthorizationError.PERMISSION_DENIED, reason="approved server list unreadable"))
        body = json.loads(request.mcp_response)
        body["tools"] = self.keep_pinned(servers, list(request.service_names), body.get("tools", []))
        return protos.McpResponseResult(mutated=json.dumps(body).encode())

    def keep_pinned(self, servers: dict[str, ApprovedServer], targets: list[str], tools: list[dict]) -> list[dict]:
        """Drop every tool from the list whose definition is not the pinned one, and raise an alert for it.

        With more than one server the gateway names each tool `<server>_<tool>`; the pin is on the
        server's own name, so the prefix is taken off before hashing.
        """
        kept = []
        for tool in tools:
            server_name = targets[0] if len(targets) == 1 else next(
                (target for target in sorted(targets, key=len, reverse=True) if tool["name"].startswith(f"{target}_")), "")
            server = servers.get(server_name)
            if not server:
                continue
            native = {**tool, "name": tool["name"].removeprefix(f"{server_name}_") if len(targets) > 1 else tool["name"]}
            row = compare(server, [native])[0]
            if row["state"] == "pinned":
                kept.append(tool)
            elif row["state"] == "changed":
                self.alerts.raise_for(row)
        return kept


async def start(guard: PinGuard | None = None, port: int = GUARD_PORT) -> grpc.aio.Server:
    server = grpc.aio.server()
    services.add_ExtMcpServicer_to_server(guard or PinGuard(), server)
    server.add_insecure_port(f"{HOST}:{port}")
    await server.start()
    return server
