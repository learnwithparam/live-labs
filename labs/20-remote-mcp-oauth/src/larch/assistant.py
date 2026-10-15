"""The assistant: an MCP client that signs in as one person, then lets a model use Larch's tools.

Connecting is the part this lab is about. The SDK's `OAuthClientProvider` does the whole OAuth 2.1
dance when the server answers 401: it reads the protected resource metadata, finds the authorization
server, registers itself, sends the person to consent with a PKCE challenge, and trades the code for
a token bound to this server. `Consent` stands in for the person's browser at the one step that needs
a human: ticking scopes and pressing Allow.
"""

import json
import logging
from typing import Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

import httpx2
from mcp.client import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from larch.config import CALLBACK, PRICES

CLIENT_NAME = "Larch Assistant"
MAX_STEPS = 8
TEMPERATURE = 0

# The SDK logs each failed tool call with a traceback; the assistant already reports refusals.
logging.getLogger("mcp").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)

Decide = Callable[[dict], Awaitable[list[str]]]


class MemoryTokens:
    """The SDK's TokenStorage, in memory: one person's registration and tokens, reused across requests."""

    def __init__(self):
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


class Consent:
    """The person's browser between the authorize redirect and the callback.

    `decide` sees what the consent screen shows and returns the scopes the person ticks. The CLI
    answers from data/consents.json; the page waits for a click.
    """

    def __init__(self, person: str, decide: Decide):
        self.person, self.decide, self.callback = person, decide, {}

    async def redirect(self, authorize_url: str) -> None:
        async with httpx2.AsyncClient(timeout=10) as browser:
            # The authorization server answers with a redirect to its consent screen.
            consent_url = (await browser.get(authorize_url)).headers["location"]
            view = (await browser.get(consent_url)).json()
            ticked = await self.decide(view)
            allowed = await browser.post(consent_url.split("?")[0], json={"request": view["request"], "person": self.person, "scopes": ticked})
        # The redirect back to the client carries the code, the state it sent, and the issuer (RFC 9207).
        self.callback = {name: values[0] for name, values in parse_qs(urlsplit(allowed.json()["redirect"]).query).items()}

    async def returned(self) -> AuthorizationCodeResult:
        if "error" in self.callback:
            raise PermissionError(f"Consent refused: {self.callback['error']}")
        return AuthorizationCodeResult(code=self.callback.get("code"), state=self.callback.get("state"), iss=self.callback.get("iss"))


def signed_in(server_url: str, consent: Consent, storage: MemoryTokens) -> httpx2.AsyncClient:
    """An HTTP client that gets, refreshes and attaches the person's token on its own."""
    auth = OAuthClientProvider(
        server_url=server_url,
        # No scope here: the MCP spec has the client ask for what the server advertises, so the server
        # decides what the consent screen lists, and the person decides what to tick.
        client_metadata=OAuthClientMetadata(client_name=CLIENT_NAME, redirect_uris=[CALLBACK],
                                            grant_types=["authorization_code", "refresh_token"], response_types=["code"]),
        storage=storage, redirect_handler=consent.redirect, callback_handler=consent.returned)
    return httpx2.AsyncClient(auth=auth, timeout=httpx2.Timeout(30, read=120))


def system_prompt(person: dict) -> str:
    return (f"You work inside Larch, Copperline Payments' issue tracker, for {person['name']} ({person['title']}). "
            "Do what they ask with the tools, then say in one or two sentences what you did. If a tool refuses, "
            "do not look for another way to do it: tell them what the refusal said. Today is 2026-10-15.")


def openai_tool(tool) -> dict:
    """An MCP tool, in the shape the model's API takes. MCP already speaks JSON Schema."""
    return {"type": "function", "function": {"name": tool.name, "description": tool.description or "",
                                             "parameters": tool.input_schema}}


def usage_of(response, model: str) -> dict:
    """What the provider reported for one call. The bill is the truth; list prices are the fallback."""
    usage = response.usage
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    # OpenRouter adds the billed cost when asked with usage.include; other providers do not.
    billed = (getattr(usage, "model_extra", None) or {}).get("cost")
    price_in, price_cached, price_out = PRICES.get(model, (0.0, 0.0, 0.0))
    listed = ((usage.prompt_tokens - cached) * price_in + cached * price_cached + usage.completion_tokens * price_out) / 1e6
    return {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "cost": float(billed) if billed is not None else listed}


async def ask(server_url: str, http: httpx2.AsyncClient | None, person: dict, request: str, client, model: str,
              on_step: Callable[[dict], None] = lambda step: None) -> dict:
    """Run one person's request through one MCP server, to a reply or to MAX_STEPS model calls.

    `http` is the signed-in client for the OAuth server, or None for the shared-key server, which
    asks nobody who they are.
    """
    calls, spent = [], {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    async with Client(streamable_http_client(server_url, http_client=http)) as mcp:
        tools = [openai_tool(tool) for tool in (await mcp.list_tools()).tools]
        messages = [{"role": "system", "content": system_prompt(person)}, {"role": "user", "content": request}]
        for _ in range(MAX_STEPS):
            response = await client.chat.completions.create(model=model, messages=messages, tools=tools,
                                                            temperature=TEMPERATURE, extra_body={"usage": {"include": True}})
            for key, value in usage_of(response, model).items():
                spent[key] += value
            said = response.choices[0].message
            if not said.tool_calls:
                on_step({"kind": "reply", "text": said.content or ""})
                return {"reply": said.content or "", "calls": calls, **spent}
            messages.append({"role": "assistant", "content": said.content, "tool_calls": [
                {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}}
                for call in said.tool_calls]})
            for call in said.tool_calls:
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = await mcp.call_tool(call.function.name, arguments)
                text = "\n".join(block.text for block in result.content if getattr(block, "type", "") == "text")
                # A refusal is still a result: the model reads why, and tells the person.
                calls.append({"tool": call.function.name, "args": arguments, "error": bool(result.is_error), "result": text[:1500]})
                on_step({"kind": "call", **calls[-1]})
                messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
    on_step({"kind": "reply", "text": ""})
    return {"reply": "", "calls": calls, **spent, "stopped": "steps"}
