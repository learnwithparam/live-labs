"""Runs Larch's servers on real local ports for as long as a command needs them.

The authorization server and both MCP servers are separate web apps on separate ports, so the client
discovers and calls them over HTTP exactly as it would a remote server. They share one process and one
workspace only so that the review can read the audit log afterwards.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

import uvicorn

from larch.authserver import LarchAuthorization, app
from larch.config import AUTH_PORT, HOST, MCP_PORT, SHARED_KEY_PORT
from larch.remote import oauth_server, shared_key_server
from larch.tracker import Workspace


@dataclass
class Stack:
    workspace: Workspace
    authority: LarchAuthorization


@asynccontextmanager
async def running(workspace: Workspace | None = None):
    """Start the authorization server, the OAuth MCP server and the shared-key MCP server; stop all three."""
    workspace = workspace or Workspace()
    authority = LarchAuthorization(workspace)
    apps = [(app(authority), AUTH_PORT),
            (oauth_server(workspace, authority.resource_secret).streamable_http_app(host=HOST), MCP_PORT),
            # The naive server gets its key the way teams do: an admin creates one in settings and pastes it in.
            (shared_key_server(workspace, workspace.create_admin_api_key()).streamable_http_app(host=HOST), SHARED_KEY_PORT)]
    servers = [uvicorn.Server(uvicorn.Config(web_app, host=HOST, port=port, log_level="warning")) for web_app, port in apps]
    tasks = [asyncio.create_task(server.serve()) for server in servers]
    try:
        while not all(server.started for server in servers):
            if any(task.done() for task in tasks):
                raise SystemExit(f"A Larch server could not start. Is port {AUTH_PORT}, {MCP_PORT} or {SHARED_KEY_PORT} in use?")
            await asyncio.sleep(0.05)
        yield Stack(workspace, authority)
    finally:
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
