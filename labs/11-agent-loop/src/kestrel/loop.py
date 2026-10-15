"""The agent loop from scratch: a model, some tools, and a loop with a budget.

That is all an agent is. Ask the model; if it asks for tools, run them and hand back the results;
ask again; stop when it answers in words or the budget runs out. Google ADK, the OpenAI Agents SDK
and Claude's tool runner all run this loop for you. Writing it once shows what they decide on your
behalf: the tool schema, the message history, when to stop.
"""

import inspect
import json
import re
from typing import Callable, get_type_hints

from kestrel.budget import Budget

JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}
# Support should act the same way on the same chat. Both engines use it, so the review compares like with like.
TEMPERATURE = 0


def tool_schema(tool: Callable) -> dict:
    """Describe a Python function as a tool the model can call, from its type hints and docstring.

    ADK builds the same schema from the same two places, which is why a tool's docstring is a prompt.
    """
    doc = inspect.getdoc(tool) or ""
    hints = {name: hint for name, hint in get_type_hints(tool).items() if name != "return"}
    # The "Args:" lines become each parameter's description.
    args = dict(re.findall(r"^\s+(\w+): (.+)$", doc.partition("Args:")[2], re.M))
    props = {name: {"type": JSON_TYPES[hint], "description": args.get(name, "")} for name, hint in hints.items()}
    return {"type": "function", "function": {
        "name": tool.__name__, "description": doc.partition("\n\nArgs:")[0],
        "parameters": {"type": "object", "properties": props, "required": list(props)}}}


def run(message: str, system: str, tools: list[Callable], client, model: str, budget: Budget,
        hand_off: Callable[[str], dict], on_step: Callable[[dict], None] = lambda step: None) -> dict:
    """Run one chat to a reply or to the edge of its budget."""
    by_name = {tool.__name__: tool for tool in tools}
    schemas = [tool_schema(tool) for tool in tools]
    # The whole memory of the agent: every message, call and result so far, sent again on each step.
    messages = [{"role": "system", "content": system}, {"role": "user", "content": message}]
    while True:
        if reason := budget.over():
            # Out of budget is a decision, not a crash: a person gets the chat with the reason.
            hand_off(reason)
            on_step({"kind": "handoff", "by": "budget", "reason": reason})
            return {"reply": "I am passing this to a colleague who will reply within 4 hours.", "stopped": "budget"}
        response = client.chat.completions.create(model=model, messages=messages, tools=schemas, temperature=TEMPERATURE)
        budget.charge(response.usage.prompt_tokens, response.usage.completion_tokens)
        on_step({"kind": "budget", **budget.state()})
        said = response.choices[0].message
        if not said.tool_calls:
            on_step({"kind": "reply", "text": said.content or ""})
            return {"reply": said.content or "", "stopped": "reply"}
        # Keep the model's own request in the history, so the next step sees what it asked for.
        messages.append({"role": "assistant", "content": said.content, "tool_calls": [
            {"id": tool_call.id, "type": "function", "function": {"name": tool_call.function.name, "arguments": tool_call.function.arguments}}
            for tool_call in said.tool_calls]})
        for call in said.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            on_step({"kind": "call", "tool": call.function.name, "args": args})
            called_tool = by_name.get(call.function.name)
            # A bad name or bad arguments go back to the model as a result it can recover from.
            try:
                result = called_tool(**args) if called_tool else {"error": f"No tool named {call.function.name}."}
            except TypeError as error:
                result = {"error": f"Bad arguments: {error}"}
            on_step({"kind": "result", "tool": call.function.name, "result": result})
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
