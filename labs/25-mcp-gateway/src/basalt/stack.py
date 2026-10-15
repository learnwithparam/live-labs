"""Runs everything on real local ports for as long as a command needs it.

Five MCP servers, the pin check and agentgateway each listen on their own port, so an agent reaches
them over HTTP exactly as it would in production. The servers share one process only so a run can
be scored from the ledger of what they actually executed.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx2
import uvicorn
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from basalt import catalogue, gateway, guard, identity
from basalt.agent import innermost, result_text
from basalt.config import CATALOGUE, GATEWAY_URL, HOST, OUT, SERVER_PORTS
from basalt.servers import Fleet, build_fleet, recorded


@dataclass
class Stack:
    fleet: Fleet
    pin_check: guard.PinGuard
    pin_check_server: object
    gateway_process: object


@asynccontextmanager
async def servers_only(fleet: Fleet | None = None):
    """Start the five MCP servers; stop them however the block ends."""
    fleet = fleet or build_fleet()
    apps = [(recorded(name, server.streamable_http_app(host=HOST), fleet.ledger), SERVER_PORTS[name])
            for name, server in fleet.servers.items()]
    web_servers = [uvicorn.Server(uvicorn.Config(web_app, host=HOST, port=port, log_level="warning")) for web_app, port in apps]
    tasks = [asyncio.create_task(web_server.serve()) for web_server in web_servers]
    try:
        while not all(web_server.started for web_server in web_servers):
            if any(task.done() for task in tasks):
                raise SystemExit(f"An MCP server could not start. Is a port in {sorted(SERVER_PORTS.values())} in use?")
            await asyncio.sleep(0.05)
        yield fleet
    finally:
        for web_server in web_servers:
            web_server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)


@asynccontextmanager
async def running(fleet: Fleet | None = None):
    """The servers, the pin check, then the gateway on top, in the order each needs the one before."""
    async with servers_only(fleet) as fleet:
        pin_check = guard.PinGuard()
        pin_check_server = await guard.start(pin_check)
        try:
            async with gateway.running(catalogue.load(CATALOGUE)) as process:
                yield Stack(fleet, pin_check, pin_check_server, process)
        finally:
            await pin_check_server.stop(0)


async def try_as(person: str, server_tool: str, arguments: dict) -> str:
    """One call through the gateway as a staff member, with no model: "" when it ran, else why it was refused."""
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {identity.token_for(person)}"},
                                  timeout=httpx2.Timeout(10, read=30)) as http:
        try:
            async with Client(streamable_http_client(GATEWAY_URL, http_client=http)) as session:
                result = await session.call_tool(server_tool, arguments)
                return "" if not result.is_error else result_text(result)
        except Exception as error:
            return innermost(error)


async def fail_closed() -> list[dict]:
    """Break each thing the gateway depends on and check that a normal call is refused, not let through.

    A gateway that allows calls when its policy is missing is worse than none: it looks safe.
    """
    probe = ("maya.lindqvist@basaltpay.com", "payments_find_payment", {"payment_id": "PAY-88412"})
    checks = []
    async with running() as stack:
        ledger = stack.fleet.ledger

        async def attempt(case: str) -> None:
            # Whether the server ran the call is read from the server, not from the reply.
            before = len(ledger.calls)
            reason = await try_as(*probe)
            checks.append({"case": case, "refused": reason, "ran": len(ledger.calls) > before})

        await attempt("everything up")
        # The list is replaced by a broken copy the pin check reads instead; the real file is never touched.
        broken = OUT / "approved-servers.broken.toml"
        broken.parent.mkdir(exist_ok=True)
        broken.write_text(CATALOGUE.read_text()[:200])
        stack.pin_check.catalogue = broken
        await attempt("approved list unreadable")
        stack.pin_check.catalogue = CATALOGUE
        await stack.pin_check_server.stop(0)
        await attempt("pin check down")
    return checks
