"""Larch's remote MCP server, built two ways over the same seven tools.

`shared_key_server` is how most teams start: the server holds one admin API key and every user's
assistant calls the API through it. The tools work, and every action is the bot's, with the bot's
rights. A read-only user can close issues, and anyone can read a private team.

`oauth_server` is how Linear, Sentry and Atlassian ship theirs. The server is an OAuth resource
server: it refuses a request without a bearer token, says where to get one (protected resource
metadata), checks each token with the authorization server, and refuses a token minted for another
audience. Each tool call then exchanges the person's token for a one-scope API token. The API acts as
that person, so the audit log names them and the client they used.
"""

from typing import Awaitable, Callable

import httpx2
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from larch.config import ISSUER, RESOURCE
from larch.tracker import Refused, Workspace

READ_ONLY = ToolAnnotations(readOnlyHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
INSTRUCTIONS = ("Larch issue tracker for the Copperline Payments workspace. Teams: PLT Platform, SUP Support, "
                "SEC Security. Issue ids look like PLT-214.")

CallApi = Callable[..., Awaitable[object]]


def register_tools(mcp: MCPServer, call_api: CallApi) -> None:
    """The same tools on both servers, so the only difference the review sees is whose rights they use."""

    @mcp.tool(annotations=READ_ONLY)
    async def search_issues(team: str = "", status: str = "", assignee: str = "", text: str = "",
                            include_done: bool = False) -> dict:
        """Find issues, newest first. Done issues are left out unless include_done is true.

        Args:
            team: A team key or name, like "PLT" or "Support". Empty searches every team you can see.
            status: Todo, In Progress, In Review or Done.
            assignee: A person's name, or "me" for the person you act for.
            text: Words from the issue title.
            include_done: Also return issues that are Done.
        """
        return {"issues": await call_api("search_issues", team=team or None, status=status or None,
                                         assignee=assignee or None, text=text or None, include_done=include_done)}

    @mcp.tool(annotations=READ_ONLY)
    async def get_issue(issue_id: str) -> dict:
        """Read one issue with its status, assignee, priority and comments.

        Args:
            issue_id: The issue id, like "PLT-214".
        """
        return await call_api("get_issue", issue_id=issue_id)

    @mcp.tool(annotations=WRITE)
    async def create_issue(team: str, title: str, priority: str = "Medium") -> dict:
        """Open a new issue in a team, in Todo.

        Args:
            team: The team key, like "SUP".
            title: One line saying what is wrong.
            priority: Urgent, High, Medium or Low.
        """
        return await call_api("create_issue", team=team, title=title, priority=priority)

    @mcp.tool(annotations=WRITE)
    async def set_status(issue_id: str, status: str) -> dict:
        """Move an issue to Todo, In Progress, In Review or Done. Closing an issue means Done.

        Args:
            issue_id: The issue id, like "SUP-88".
            status: Todo, In Progress, In Review or Done.
        """
        return await call_api("set_status", issue_id=issue_id, status=status)

    @mcp.tool(annotations=WRITE)
    async def assign_issue(issue_id: str, assignee: str) -> dict:
        """Give an issue to a person.

        Args:
            issue_id: The issue id.
            assignee: The person's name, like "Maya", or "me".
        """
        return await call_api("assign_issue", issue_id=issue_id, assignee=assignee)

    @mcp.tool(annotations=WRITE)
    async def add_comment(issue_id: str, body: str) -> dict:
        """Comment on an issue. The comment is signed by whoever the server acts as.

        Args:
            issue_id: The issue id.
            body: The comment, in the person's words.
        """
        return await call_api("add_comment", issue_id=issue_id, body=body)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_issue(issue_id: str) -> dict:
        """Delete an issue for good. Ask before using it.

        Args:
            issue_id: The issue id.
        """
        return await call_api("delete_issue", issue_id=issue_id)


def refused_as_tool_error(refusal: Refused) -> ToolError:
    # The model reads this text, so it says what happened and what the person can do about it.
    return ToolError(f"Larch refused ({refusal.status}): {refusal.reason} Nothing was changed.")


def shared_key_server(workspace: Workspace, api_key: str) -> MCPServer:
    """The naive server: no sign-in, one admin key, every call made as the integration's service account."""
    mcp = MCPServer("larch-shared-key", instructions=INSTRUCTIONS)

    async def call_api(action: str, **args) -> object:
        try:
            return workspace.call(api_key, action, **args)
        except Refused as refusal:
            raise refused_as_tool_error(refusal) from None

    register_tools(mcp, call_api)
    return mcp


class IntrospectionVerifier:
    """Checks each bearer token with the authorization server (RFC 7662) instead of trusting it.

    It hands the token's audience to the SDK, which refuses any token not issued for RESOURCE.
    """

    def __init__(self, resource_secret: str):
        self.resource_secret = resource_secret

    async def verify_token(self, token: str) -> AccessToken | None:
        async with httpx2.AsyncClient(timeout=10) as http:
            response = await http.post(f"{ISSUER}/introspect", data={"token": token},
                                       headers={"Authorization": f"Bearer {self.resource_secret}"})
        claims = response.json() if response.status_code == 200 else {}
        if not claims.get("active"):
            return None
        return AccessToken(token=token, client_id=claims["client_id"], scopes=claims["scope"].split(),
                           expires_at=claims["exp"], resource=claims["aud"], subject=claims["sub"])


def oauth_server(workspace: Workspace, resource_secret: str) -> MCPServer:
    """The server as an OAuth 2.1 resource server. It holds no API key of its own."""
    # No required scopes: each tool checks its own at the token exchange. A client then asks for what the
    # authorization server lists, and the person's consent and role cut it down.
    auth = AuthSettings(issuer_url=ISSUER, resource_server_url=RESOURCE, validate_token_resource=True)
    mcp = MCPServer("larch", instructions=INSTRUCTIONS, auth=auth,
                    token_verifier=IntrospectionVerifier(resource_secret))

    async def call_api(action: str, **args) -> object:
        person_token = get_access_token()
        if person_token is None:
            raise ToolError("No signed-in person on this request.")
        # Never pass the person's token through to the API: it was issued for this server, not the API.
        async with httpx2.AsyncClient(timeout=10) as http:
            response = await http.post(f"{ISSUER}/exchange", data={"subject_token": person_token.token, "action": action, "issue": args.get("issue_id") or ""},
                                       headers={"Authorization": f"Bearer {resource_secret}"})
        if response.status_code != 200:
            raise ToolError(response.json().get("error_description", "The token exchange failed."))
        try:
            return workspace.call(response.json()["access_token"], action, **args)
        except Refused as refusal:
            raise refused_as_tool_error(refusal) from None

    register_tools(mcp, call_api)
    return mcp
