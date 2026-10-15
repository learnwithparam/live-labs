"""The store assistant: one model, connected to an MCP server, doing what the merchant asks.

The assistant knows nothing about Tidepool's admin. It asks the server for its tool list, hands
those names, descriptions and schemas to the model, and runs whatever the model calls. So the same
assistant, with the same prompt, gets better or worse only because the tools did. That makes the
tools the thing to design, and the thing to measure.
"""

import json
import logging
from typing import Callable

from mcp.client import Client
from mcp.server.mcpserver import MCPServer

SYSTEM = (
    "You are the assistant inside Tidepool Coffee's store admin. The merchant asks for a change or a "
    "fact; use the tools to do it, then say in one or two sentences what you did. Act without asking "
    "for confirmation, except when the request could mean more than one product or variant: then "
    "change nothing and ask the merchant which one. Today is 2026-10-15."
)
MAX_STEPS = 12
# Enough to keep a find_products answer whole, so the admin can offer its variants as choices.
RESULT_KEPT = 2000
TEMPERATURE = 0

# The SDK logs every failed tool call on the server side, with a traceback for a raised exception.
# The assistant reports failures itself, so the server's log would only print each one twice.
logging.getLogger("mcp").setLevel(logging.CRITICAL)
# The SDK turns on INFO logging, which would print a line for every HTTP request to the model.
logging.getLogger("httpx").setLevel(logging.WARNING)


def openai_tool(tool) -> dict:
    """An MCP tool, as the tool format the model's API takes. MCP already speaks JSON Schema."""
    return {"type": "function", "function": {"name": tool.name, "description": tool.description or "",
                                             "parameters": tool.input_schema}}


def result_text(result) -> str:
    """The text a tool returned, which is all the model gets to read, error or not."""
    return "\n".join(block.text for block in result.content if getattr(block, "type", "") == "text")


async def ask(server: MCPServer, request: str, client, model: str,
              on_step: Callable[[dict], None] = lambda step: None, history: list[dict] | None = None) -> dict:
    """Run one merchant request against one MCP server, to a reply or to MAX_STEPS model calls.

    `history` holds the earlier turns as text, so the merchant can answer "which one?" with "the 1kg".
    """
    calls, tokens = [], {"prompt": 0, "completion": 0}
    # In process for speed; the same Client takes a URL or a stdio command for a server elsewhere.
    async with Client(server) as mcp:
        tools = [openai_tool(tool) for tool in (await mcp.list_tools()).tools]
        messages = [{"role": "system", "content": SYSTEM}, *(history or []), {"role": "user", "content": request}]
        for _ in range(MAX_STEPS):
            response = await client.chat.completions.create(model=model, messages=messages, tools=tools,
                                                            temperature=TEMPERATURE)
            tokens["prompt"] += response.usage.prompt_tokens
            tokens["completion"] += response.usage.completion_tokens
            said = response.choices[0].message
            if not said.tool_calls:
                on_step({"kind": "reply", "text": said.content or ""})
                return {"reply": said.content or "", "calls": calls, "tokens": tokens, "tools": len(tools)}
            messages.append({"role": "assistant", "content": said.content, "tool_calls": [
                {"id": call.id, "type": "function",
                 "function": {"name": call.function.name, "arguments": call.function.arguments}}
                for call in said.tool_calls]})
            for call in said.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await mcp.call_tool(call.function.name, args)
                text = result_text(result)
                # An error result is still a result: the model reads it and decides what to do next.
                calls.append({"tool": call.function.name, "args": args, "error": bool(result.is_error), "result": text[:RESULT_KEPT]})
                on_step({"kind": "call", **calls[-1]})
                messages.append({"role": "tool", "tool_call_id": call.id, "content": text})
    on_step({"kind": "reply", "text": ""})
    return {"reply": "", "calls": calls, "tokens": tokens, "tools": len(tools), "stopped": "steps"}
