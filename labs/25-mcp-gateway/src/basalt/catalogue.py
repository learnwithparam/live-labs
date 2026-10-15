"""The approved server list: the one file that says which MCP servers Basalt staff agents may use.

It is the truth outside the model. A server is reachable only if it is listed here, a team may call
only the tools listed for it, and every tool carries the SHA-256 of the definition a reviewer read.
The gateway's routes and rules are generated from this file, and the pin check reads it on every call,
so there is no second copy to drift out of step.

Why hash the definition: the model reads a tool's description as instructions. A server that keeps
a tool's name but rewrites its description (a "rug-pull") changes what the agent will do without
changing anything a name-based allow-list can see.
"""

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from basalt.config import CATALOGUE, DATA


class CatalogueUnreadable(Exception):
    """The list is missing or malformed. Every caller treats this as "refuse", never as "allow all"."""


@dataclass
class PinnedTool:
    name: str
    teams: list[str]
    sha256: str


@dataclass
class ApprovedServer:
    name: str
    url: str
    owner: str
    owning_team: str
    approved_by: str
    approved_on: str
    tools: dict[str, PinnedTool] = field(default_factory=dict)


def tool_hash(tool: dict) -> str:
    """The pin: a hash of exactly what the model reads, so any change to it changes the hash.

    Keys are sorted and spacing fixed, so the same definition hashes the same whether it came straight
    from the server or through the gateway.
    """
    readable = {"name": tool["name"], "description": tool.get("description") or "",
                "inputSchema": tool.get("inputSchema") or tool.get("input_schema") or {}}
    return hashlib.sha256(json.dumps(readable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load(path: Path = CATALOGUE) -> dict[str, ApprovedServer]:
    """Read the list, or raise CatalogueUnreadable. A list that half-parses is not a list."""
    try:
        raw = tomllib.loads(path.read_text())
        servers = {}
        for entry in raw["server"]:
            tools = {tool["name"]: PinnedTool(tool["name"], list(tool["teams"]), tool["sha256"]) for tool in entry["tool"]}
            servers[entry["name"]] = ApprovedServer(entry["name"], entry["url"], entry["owner"], entry["owning_team"],
                                                    entry["approved_by"], entry["approved_on"], tools)
        return servers
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        raise CatalogueUnreadable(f"{path.name}: {error!r}") from error


def render(servers: dict[str, ApprovedServer]) -> str:
    """Write the list back as TOML a reviewer can read in a pull request, one tool per block."""
    lines = ["# Basalt Pay's approved MCP servers. The platform team reviews every change to this file.",
             "# `sha256` pins each tool's definition as the reviewer read it: a changed description is blocked.", ""]
    for server in servers.values():
        lines += ["[[server]]", *(f"{key} = {json.dumps(getattr(server, key))}" for key in
                                  ("name", "url", "owner", "owning_team", "approved_by", "approved_on")), ""]
        for tool in server.tools.values():
            lines += ["[[server.tool]]", f"name = {json.dumps(tool.name)}", f"teams = {json.dumps(tool.teams)}",
                      f"sha256 = {json.dumps(tool.sha256)}", ""]
    return "\n".join(lines)


def save(servers: dict[str, ApprovedServer], path: Path = CATALOGUE) -> None:
    # Write then rename, so the gateway's pin check never reads half a file and refuses everything.
    staging = path.with_suffix(".toml.tmp")
    staging.write_text(render(servers))
    staging.replace(path)


def allowed_tools(servers: dict[str, ApprovedServer], team: str) -> dict[str, list[str]]:
    """Each approved server's tools that one team may call."""
    return {server.name: [tool.name for tool in server.tools.values() if team in tool.teams] for server in servers.values()}


async def live_definitions(url: str, headers: dict[str, str] | None = None) -> list[dict]:
    """Ask a server for its tools right now, as plain dicts, the way any MCP client would."""
    async with httpx2.AsyncClient(headers=headers or {}, timeout=httpx2.Timeout(10, read=30)) as http:
        async with Client(streamable_http_client(url, http_client=http)) as mcp:
            return [tool.model_dump(by_alias=True, exclude_none=True) for tool in (await mcp.list_tools()).tools]


def compare(server: ApprovedServer, live: list[dict]) -> list[dict]:
    """Each live tool against its pin: "pinned" when it matches, else "changed" or "not on the list"."""
    rows = []
    for tool in live:
        pinned = server.tools.get(tool["name"])
        current = tool_hash(tool)
        state = "not on the list" if not pinned else "pinned" if pinned.sha256 == current else "changed"
        rows.append({"server": server.name, "tool": tool["name"], "state": state, "current": current,
                     "pinned": pinned.sha256 if pinned else None, "description": tool.get("description") or ""})
    return rows


def staff() -> dict[str, dict]:
    return {person["email"]: person for person in tomllib.loads((DATA / "staff.toml").read_text())["person"]}


def approve(servers: dict[str, ApprovedServer], name: str, url: str, owner: str, owning_team: str,
            reviewer: str, tools: list[dict]) -> dict[str, ApprovedServer]:
    """A reviewer's yes: list the server and pin each tool exactly as the reviewer saw it."""
    pinned = {tool["name"]: PinnedTool(tool["name"], tool["teams"], tool_hash(tool)) for tool in tools if tool["teams"]}
    servers[name] = ApprovedServer(name, url, owner, owning_team, reviewer, date.today().isoformat(), pinned)
    return servers
