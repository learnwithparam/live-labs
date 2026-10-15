"""Larch's authorization server: who the assistant may act as, and with which scopes.

An MCP client never sees a password and never holds an admin key. It registers itself, sends the
person to Larch's consent screen with a PKCE challenge, and gets back a token bound to one person,
the scopes that person ticked, and one audience: the MCP server. The SDK's routes handle the OAuth
wire format (metadata, registration, PKCE checks); this module decides what each step means.

The MCP server then trades that token for a narrower one before it touches Larch's API, the way an
OAuth token exchange (RFC 8693) works. The downstream token carries one scope and lives a minute, so a
leaked one is worth little, and the API checks it again.
"""

import secrets
import time

from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
                                      RefreshToken, TokenError, construct_redirect_uri)
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from larch.config import ISSUER, RESOURCE
from larch.tracker import NEEDS, Credential, Workspace

# Short lives limit the damage of a leaked token. The client refreshes; the person is not asked again.
CODE_SECONDS = 300
ACCESS_SECONDS = 15 * 60
DOWNSTREAM_SECONDS = 60
# The audience a downstream token is for: Larch's own API, not the MCP server.
API_AUDIENCE = "https://api.larch.example"


class ExchangeRefused(Exception):
    """The person never granted what this tool call needs. The message says what to ask them for."""


class LarchAuthorization:
    """The SDK's OAuthAuthorizationServerProvider, for one Larch workspace.

    Everything is held in memory: a restart signs every client out, which is the safe direction.
    """

    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.pending: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams]] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.tokens: dict[str, AccessToken] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}
        self.downstream: dict[str, Credential] = {}
        # (person, client) to the scopes they ticked: what the page shows as "connected apps".
        self.grants: dict[tuple[str, str], list[str]] = {}
        # The API trusts what this server minted, and nothing else besides its own keys.
        workspace.trusted.append(self.downstream.get)
        # The MCP server authenticates to introspection and exchange with this, like a client secret.
        self.resource_secret = secrets.token_urlsafe(24)

    # Dynamic client registration: any MCP client can introduce itself, but gets nothing until a person consents.

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.clients[client_info.client_id] = client_info

    # The authorization request: park it, and send the browser to the consent screen.

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        # A token with no audience could be replayed at any server that trusts this issuer.
        if (params.resource or "").rstrip("/") != RESOURCE:
            raise AuthorizeError("invalid_target", f"This server issues tokens for {RESOURCE} only.")
        request_id = secrets.token_urlsafe(16)
        self.pending[request_id] = (client, params)
        return f"{ISSUER}/consent?request={request_id}"

    def consent_view(self, request_id: str) -> dict:
        """What the consent screen shows: the app, the server it wants, and each scope in plain words."""
        client, params = self.pending[request_id]
        described = self.workspace.data["scopes"]
        return {"request": request_id, "client": client.client_name or client.client_id, "resource": params.resource,
                "scopes": [{"scope": scope, "means": described.get(scope, scope)} for scope in params.scopes or []],
                "people": [{"id": user["id"], "name": user["name"], "can_grant": self.workspace.role_scopes(user["id"])}
                           for user in self.workspace.data["users"]]}

    def approve(self, request_id: str, person: str, ticked: list[str]) -> str:
        """The person pressed Allow. Returns the redirect back to the client, with a code or an error.

        The grant is what the client asked for, cut to what the person ticked, cut again to what
        their role holds. A support lead cannot grant delete by ticking a box.
        """
        client, params = self.pending.pop(request_id)
        granted = [scope for scope in params.scopes or [] if scope in ticked and scope in self.workspace.role_scopes(person)]
        if not granted:
            return construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state, iss=ISSUER)
        code = secrets.token_urlsafe(32)
        self.codes[code] = AuthorizationCode(
            code=code, scopes=granted, expires_at=time.time() + CODE_SECONDS, client_id=client.client_id,
            code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource, subject=person)
        self.grants[(person, client.client_name or client.client_id)] = granted
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state, iss=ISSUER)

    # The token endpoint. The SDK has already checked the PKCE verifier against the challenge.

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        return self.codes.get(authorization_code)

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        # A code works once. A second use means it leaked, so it buys nothing.
        self.codes.pop(authorization_code.code, None)
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource, authorization_code.subject)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        found = self.refresh_tokens.get(refresh_token)
        return found if found and found.client_id == client.client_id else None

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        # A refresh may narrow the grant, never widen it: widening needs the person again.
        if any(scope not in refresh_token.scopes for scope in scopes):
            raise TokenError("invalid_scope", "A refresh cannot add scopes the person did not grant.")
        self.refresh_tokens.pop(refresh_token.token, None)
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource, refresh_token.subject)

    def _issue(self, client_id: str, scopes: list[str], resource: str | None, subject: str | None) -> OAuthToken:
        access, refresh = "lat_" + secrets.token_urlsafe(32), "lrt_" + secrets.token_urlsafe(32)
        self.tokens[access] = AccessToken(token=access, client_id=client_id, scopes=scopes, resource=resource,
                                          subject=subject, expires_at=int(time.time()) + ACCESS_SECONDS)
        self.refresh_tokens[refresh] = RefreshToken(token=refresh, client_id=client_id, scopes=scopes,
                                                    resource=resource, subject=subject)
        return OAuthToken(access_token=access, expires_in=ACCESS_SECONDS, scope=" ".join(scopes), refresh_token=refresh)

    async def load_access_token(self, token: str) -> AccessToken | None:
        found = self.tokens.get(token)
        return found if found and found.expires_at and found.expires_at > time.time() else None

    async def revoke_token(self, token) -> None:
        self.tokens.pop(token.token, None)
        self.refresh_tokens.pop(token.token, None)

    # What the MCP server calls: introspection to check a token, exchange to get a narrow one.

    async def introspect(self, token: str) -> dict:
        """RFC 7662: the resource server learns who the token is for, its scopes and its audience."""
        found = await self.load_access_token(token)
        if found is None:
            return {"active": False}
        return {"active": True, "sub": found.subject, "scope": " ".join(found.scopes), "aud": found.resource,
                "client_id": found.client_id, "exp": found.expires_at, "iss": ISSUER}

    async def exchange(self, subject_token: str, action: str, issue: str = "") -> str:
        """Trade the person's MCP token for a one-minute API token holding only the scope `action` needs.

        This is where "act as the person, within what they granted" is enforced before the API sees
        anything. A missing scope is refused here, and written to the audit log as a refusal.
        """
        found = await self.load_access_token(subject_token)
        if found is None or (found.resource or "").rstrip("/") != RESOURCE:
            raise ExchangeRefused("The token is expired, or was not issued for this MCP server.")
        client = self.clients[found.client_id]
        client_name = client.client_name or client.client_id
        needed = NEEDS[action]
        if needed not in found.scopes:
            person = self.workspace.name_of(found.subject)
            reason = (f"{person} granted {client_name} {', '.join(found.scopes)}. {action} needs {needed}. "
                      f"Nothing was changed. Ask {person} to reconnect and grant {needed}, or to do it in Larch.")
            # The issue is only what the MCP server says it was for; the log keeps it so a reader sees which one.
            self.workspace.record_refusal(found.subject, client_name, "OAuth token exchange", action, reason, {}, [issue] if issue else [])
            raise ExchangeRefused(reason)
        downstream = "ldt_" + secrets.token_urlsafe(24)
        self.downstream[downstream] = Credential(found.subject, frozenset([needed]), client_name, "OAuth token exchange",
                                                 expires_at=time.time() + DOWNSTREAM_SECONDS)
        return downstream


def app(authority: LarchAuthorization) -> Starlette:
    """The authorization server as a web app: the SDK's OAuth routes plus consent, introspection and exchange."""
    # AuthSettings parses the issuer without adding a trailing slash, so it matches the PRM document exactly.
    issuer = AuthSettings(issuer_url=ISSUER, resource_server_url=None).issuer_url
    routes = create_auth_routes(
        authority, issuer,
        client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=list(authority.workspace.data["scopes"]),
                                                              default_scopes=["issues:read"]),
        revocation_options=RevocationOptions(enabled=True))

    def from_resource_server(request: Request) -> bool:
        return request.headers.get("authorization") == f"Bearer {authority.resource_secret}"

    async def consent(request: Request) -> JSONResponse:
        # Larch's real consent page sits behind its own sign-in; here the person picks who they are.
        if request.method == "GET":
            request_id = request.query_params.get("request", "")
            if request_id not in authority.pending:
                return JSONResponse({"error": "unknown or used consent request"}, status_code=404)
            return JSONResponse(authority.consent_view(request_id))
        body = await request.json()
        if body.get("request") not in authority.pending or authority.workspace.user(body.get("person", "")) is None:
            return JSONResponse({"error": "a pending request and a person in this workspace"}, status_code=400)
        return JSONResponse({"redirect": authority.approve(body["request"], body["person"], list(body.get("scopes") or []))})

    async def introspect(request: Request) -> JSONResponse:
        if not from_resource_server(request):
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        form = await request.form()
        return JSONResponse(await authority.introspect(str(form.get("token", ""))))

    async def exchange(request: Request) -> JSONResponse:
        if not from_resource_server(request):
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        form = await request.form()
        if str(form.get("action")) not in NEEDS:
            return JSONResponse({"error": "invalid_request", "error_description": "unknown action"}, status_code=400)
        try:
            token = await authority.exchange(str(form.get("subject_token", "")), str(form["action"]), str(form.get("issue", "")).upper())
        except ExchangeRefused as refusal:
            return JSONResponse({"error": "insufficient_scope", "error_description": str(refusal)}, status_code=403)
        return JSONResponse({"access_token": token, "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                             "token_type": "Bearer", "expires_in": DOWNSTREAM_SECONDS, "audience": API_AUDIENCE})

    routes += [Route("/consent", consent, methods=["GET", "POST"]), Route("/introspect", introspect, methods=["POST"]),
               Route("/exchange", exchange, methods=["POST"])]
    return Starlette(routes=routes)
