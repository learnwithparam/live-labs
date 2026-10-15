"""agentgateway, the open source MCP gateway (Linux Foundation), configured from the approved list.

Staff agents connect to one URL. Behind it agentgateway multiplexes the approved servers, so a tool
reaches the agent as `payments_refund_payment`. Four of the job's rules are the gateway's own features:

1. **Only listed servers.** The targets are generated from the list. Snippet Cloud is not on it, so
   there is no route to it at all.
2. **Tools per team.** One `mcpAuthorization` rule per tool, written in CEL, matches the team in the
   staff token. The gateway hides the other tools from `tools/list` and refuses calls to them.
3. **No token pass-through.** The gateway checks the staff token (`jwtAuth`) and does not forward it;
   there is no `backendAuth: passthrough`, so upstream servers never hold a Basalt credential.
4. **One call log.** Every request is an access log line with the person, team, server and tool.

The fifth rule, the pinned hash, has no built-in switch, so it runs as an `mcpGuardrails` processor
(`basalt.guard`) with `failClosed`: when the processor cannot be reached, the gateway refuses.
"""

import asyncio
import hashlib
import json
import platform
import subprocess
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from basalt.catalogue import ApprovedServer
from basalt.config import GATEWAY_PORT, GUARD_PORT, HOST, LAB, OUT, READINESS_PORT
from basalt.identity import AUDIENCE, ISSUER, jwks_path

VERSION = "1.5.0"
# Copied from the release's .sha256 files, so a swapped download fails here instead of running.
CHECKSUMS = {
    "darwin-arm64": "da432d35bd696da0564f7b2b6bbc783542b6b9c616d6c0c4d4c3daef9dfa11a1",
    "linux-amd64": "daca5cda76e8c5ab0c1a75912fecf2d6365095403f810db72029c49d14a37e7b",
    "linux-arm64": "61f12dbb99669aa4b97b85a0040183fe4b098fa0a82a8c389665fe606517c13e",
}
BINARY = LAB / "bin" / "agentgateway"
CONFIG = OUT / "agentgateway.json"
LOG = OUT / "gateway.log"


def platform_name() -> str:
    machine = {"aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine().lower(), platform.machine().lower())
    return f"{platform.system().lower()}-{machine}"


def install() -> Path:
    """Download the pinned agentgateway release once and check its sha256 before it ever runs."""
    if BINARY.exists():
        return BINARY
    name = platform_name()
    if name not in CHECKSUMS:
        raise SystemExit(f"No agentgateway build pinned for {name}. Run cr.agentgateway.dev/agentgateway:{VERSION} in Docker.")
    url = f"https://github.com/agentgateway/agentgateway/releases/download/v{VERSION}/agentgateway-{name}"
    with urllib.request.urlopen(url, timeout=300) as response:
        binary = response.read()
    if hashlib.sha256(binary).hexdigest() != CHECKSUMS[name]:
        raise SystemExit(f"The agentgateway download from {url} does not match its pinned checksum.")
    BINARY.parent.mkdir(exist_ok=True)
    BINARY.write_bytes(binary)
    BINARY.chmod(0o755)
    return BINARY


def team_rule(server: str, tool: str, teams: list[str]) -> str:
    """One CEL rule per tool. A tool no rule matches is hidden and refused: the default is deny."""
    return f'mcp.tool.target == "{server}" && mcp.tool.name == "{tool}" && jwt.team in {json.dumps(teams)}'


def render(servers: dict[str, ApprovedServer]) -> dict:
    """The whole gateway config, generated from the approved list so the two can never disagree."""
    policies = {
        "jwtAuth": {"mode": "strict", "issuer": ISSUER, "audiences": [AUDIENCE], "jwks": {"file": str(jwks_path())}},
        "mcpAuthorization": {"rules": [team_rule(server.name, tool.name, tool.teams)
                                       for server in servers.values() for tool in server.tools.values()]},
        "mcpGuardrails": {"processors": [{
            "kind": "remote", "host": f"{HOST}:{GUARD_PORT}", "failureMode": "failClosed",
            # Each method that reaches a server is checked on the way in; the tool list also on the way out.
            "methods": {"tools/call": "request", "tools/list": "full", "prompts/get": "request",
                        "resources/read": "request"},
            "metadata": {"person": "jwt.sub", "team": "jwt.team"},
            # The pin check needs no credential either, so the staff token stops at the gateway.
            "requestHeaders": {"disallowed": ["authorization"]},
        }]},
    }
    targets = [{"name": server.name, "mcp": {"host": server.url}} for server in servers.values()]
    return {
        "config": {"adminAddr": "off", "statsAddr": "off", "readinessAddr": f"{HOST}:{READINESS_PORT}",
                   "logging": {"format": "json"}},
        "frontendPolicies": {"accessLog": {"add": {"person": "jwt.sub", "team": "jwt.team"}}},
        "binds": [{"port": GATEWAY_PORT, "listeners": [{"routes": [{"policies": policies,
                                                                     "backends": [{"mcp": {"targets": targets}}]}]}]}],
    }


def write_config(servers: dict[str, ApprovedServer]) -> Path:
    # agentgateway watches this file and applies a change without a restart, so an approval lands live.
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    staging = CONFIG.with_suffix(".tmp")
    staging.write_text(json.dumps(render(servers), indent=1))
    staging.replace(CONFIG)
    return CONFIG


def ready() -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{READINESS_PORT}/healthz/ready", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


@asynccontextmanager
async def running(servers: dict[str, ApprovedServer]):
    """Start agentgateway on the generated config, wait until it is ready, and stop it however the block ends.

    The log is appended to, never cleared, so the console's call log keeps every earlier run.
    """
    config = write_config(servers)
    with LOG.open("a") as log:
        process = subprocess.Popen([str(install()), "-f", str(config)], stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(200):
                if process.poll() is not None:
                    raise SystemExit(f"agentgateway stopped: {LOG.read_text()[-600:]}")
                if ready():
                    break
                await asyncio.sleep(0.05)
            else:
                raise SystemExit(f"agentgateway did not become ready. Is port {GATEWAY_PORT} or {READINESS_PORT} in use?")
            yield process
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def log_size() -> int:
    return LOG.stat().st_size if LOG.exists() else 0


def call_log(path: Path | None = None, since: int = 0) -> list[dict]:
    """The gateway's access log as the console's call log: one row per tool call, allowed or refused.

    A refused session (no token, or the pin check down) is a row too, so a failure is never silent.
    """
    rows = []
    path = path or LOG
    text = path.read_bytes()[since:].decode(errors="replace") if path.exists() else ""
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("scope") != "request":
            continue
        method = entry.get("mcp.method.name")
        error = entry.get("error") or entry.get("mcp.error.message")
        if method != "tools/call" and not error:
            continue
        rows.append({"time": entry.get("time", ""), "person": entry.get("person") or entry.get("jwt.sub") or "no token",
                     "team": entry.get("team") or "", "server": entry.get("mcp.target") or "",
                     "tool": entry.get("gen_ai.tool.name") or method or "", "allowed": not error,
                     "reason": plain_reason(error) if error else ""})
    return rows


def plain_reason(error: str) -> str:
    """The gateway's error, said the way the platform team would say it."""
    if "Unknown tool" in error:
        return "not on this team's tool list"
    if "mcpGuardrails rejected:" in error:
        return error.split("mcpGuardrails rejected:", 1)[1].strip()
    if "mcpGuardrails" in error:
        return "pin check unreachable, so the gateway refused (fail closed)"
    if "bearer token" in error or "authentication" in error:
        return "no valid staff token"
    return error
