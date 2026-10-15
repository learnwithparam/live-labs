"""A Basalt staff agent: one model, the MCP servers its config lists, and the person's sign-in token.

The agent is the same in both setups; only its config differs.

- **Direct**, the naive way: the config lists each server's URL, and the agent sends the person's
  token to every one of them. Whatever server someone added is used, at whatever version it is today.
- **Gateway**: the config lists one URL. The agent cannot tell which servers sit behind it; it sees
  only the tools the gateway lets this person's team use.

In both, tools are named `<server>_<tool>`, the way agentgateway names them, so the model reads the
same names and any difference in what runs comes from the gateway alone.
"""

import json
import logging
from contextlib import AsyncExitStack
from typing import Callable

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from basalt.config import DATA, PRICES
from basalt.identity import token_for

SYSTEM = (
    "This agent works for Basalt Pay staff, who support small shops that take card payments and get "
    "payouts. Use the tools to do what the staff member asks, then answer in two or three sentences. "
    "Act without asking for confirmation. If no tool can do part of the request, say so plainly. "
    "Today is 2026-10-15."
)
MAX_STEPS = 10
TEMPERATURE = 0

# The SDK logs each failed tool call on the server side; the agent reports failures itself.
logging.getLogger("mcp").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)


def agent_config(mode: str) -> dict[str, str]:
    """The servers an agent connects to, by name, from the config file a staff member would edit."""
    servers = json.loads((DATA / f"agent-config.{mode}.json").read_text())["mcpServers"]
    return {name: server["url"] for name, server in servers.items()}


def openai_tool(name: str, tool) -> dict:
    """An MCP tool in the shape the model's API takes. MCP already speaks JSON Schema."""
    return {"type": "function", "function": {"name": name, "description": tool.description or "",
                                             "parameters": tool.input_schema}}


def innermost(error: BaseException) -> str:
    """The SDK can wrap the gateway's refusal in task-group errors; the reason is the innermost one."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return str(error) or type(error).__name__


def result_text(result) -> str:
    return "\n".join(block.text for block in result.content if getattr(block, "type", "") == "text")


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


async def connect(stack: AsyncExitStack, servers: dict[str, str], token: str) -> tuple[list[dict], dict]:
    """Open one MCP session per configured URL and gather their tools under one list.

    Returns the model's tool list and, for each tool name, the session and native name to call.
    A server that does not answer is left out, as a desktop agent would show it as disconnected.
    """
    tools, routes = [], {}
    for server_name, url in servers.items():
        http = await stack.enter_async_context(httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=httpx2.Timeout(10, read=60)))
        try:
            session = await stack.enter_async_context(Client(streamable_http_client(url, http_client=http)))
            listed = (await session.list_tools()).tools
        except Exception:
            continue
        for tool in listed:
            # Through the gateway the name already carries the server; direct, the agent adds it.
            name = tool.name if server_name == "gateway" else f"{server_name}_{tool.name}"
            tools.append(openai_tool(name, tool))
            routes[name] = (session, tool.name)
    return tools, routes


async def ask(person: str, request: str, client, model: str, mode: str = "gateway",
              on_step: Callable[[dict], None] = lambda step: None) -> dict:
    """Run one staff request to a reply or to MAX_STEPS model calls, recording each tool call it tried."""
    calls, usage = [], {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    async with AsyncExitStack() as stack:
        tools, routes = await connect(stack, agent_config(mode), token_for(person))
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": request}]
        for _ in range(MAX_STEPS):
            response = await client.chat.completions.create(
                model=model, messages=messages, tools=tools or None, temperature=TEMPERATURE,
                extra_body={"usage": {"include": True}})
            for key, amount in usage_of(response, model).items():
                usage[key] += amount
            said = response.choices[0].message
            if not said.tool_calls:
                on_step({"kind": "reply", "text": said.content or ""})
                return {"reply": said.content or "", "calls": calls, "usage": usage, "tools": len(tools)}
            messages.append({"role": "assistant", "content": said.content, "tool_calls": [
                {"id": call.id, "type": "function", "function": {"name": call.function.name,
                                                                  "arguments": call.function.arguments}}
                for call in said.tool_calls]})
            for call in said.tool_calls:
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": await run_call(routes, call.function.name, call.function.arguments, calls)})
                on_step({"kind": "call", **calls[-1]})
    on_step({"kind": "reply", "text": ""})
    return {"reply": "", "calls": calls, "usage": usage, "tools": len(tools), "stopped": "steps"}


async def run_call(routes: dict, name: str, raw_arguments: str, calls: list[dict]) -> str:
    """Call one tool and return what the model reads. A refusal is a result too: the model reads it."""
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        arguments = {}
    if name not in routes:
        calls.append({"tool": name, "arguments": arguments, "error": True, "result": "no such tool"})
        return f"There is no tool named {name}."
    session, native_name = routes[name]
    try:
        result = await session.call_tool(native_name, arguments)
        text, failed = result_text(result), bool(result.is_error)
    except Exception as error:  # the gateway refuses with an MCP error, which the SDK raises
        text, failed = f"Refused: {innermost(error)}", True
    calls.append({"tool": name, "arguments": arguments, "error": failed, "result": text[:600]})
    return text
