"""One research agent: a loop that searches the library, reads sources and hands back quoted findings.

The single agent and every subagent run this same loop. Only the objective and the tool-call budget
differ, so any gap between the two versions comes from how the work is split, not a better searcher.
"""

import json
from typing import Callable

from kitebird.config import PRICES
from kitebird.corpus import Library

INSTRUCTIONS = """Research the objective in Kitebird's source library, then call `finish` with your findings.

- Start with short, broad searches, then narrow them. Skip sources that are off topic.
- `read` a source before you cite it. Prefer recent, first-hand sources. When sources disagree, keep both and say which is newer.
- Each finding is a claim, the id of the source that states it, and quotes: the whole sentences of that source that state the claim, each copied exactly.
- Every fact in the claim must appear in one of its quotes. A fact you cannot quote stays out of the claim.
- Give at most {most} findings, the ones that answer the objective best.
- You have {budget} tool calls in all. Call `finish` before they run out."""

# A claim with three facts needs three sentences behind it; more than that is a paragraph, not a citation.
MOST_QUOTES = 3


def tool(name: str, description: str, **properties: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}}}


FINDING = {"type": "object", "additionalProperties": False, "required": ["claim", "source", "quotes"], "properties": {
    "claim": {"type": "string", "description": "A factual claim in your own words."},
    "source": {"type": "string", "description": "The id of the source that states it, like S04."},
    "quotes": {"type": "array", "items": {"type": "string"}, "maxItems": MOST_QUOTES,
               "description": "The sentences of that source that state the claim, each copied exactly."}}}
TOOLS = [
    tool("search", "Search the source library. Returns up to five sources with id, publisher, date and the best-matching sentences.",
         query={"type": "string", "description": "A few keywords, like 'cargo bike dealer margin'."}),
    tool("read", "Read one source in full.", source={"type": "string", "description": "A source id from search results, like S04."}),
    tool("finish", "Hand back your findings and stop.", findings={"type": "array", "items": FINDING}),
]
FINISH_NOW = {"type": "function", "function": {"name": "finish"}}


def usage_of(response, model: str) -> dict:
    """What the provider reported for one call. The bill is the truth; list prices are the fallback."""
    usage = response.usage
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    # OpenRouter adds the billed cost when asked with usage.include; other providers do not.
    billed = (getattr(usage, "model_extra", None) or {}).get("cost")
    price_in, price_cached, price_out = PRICES.get(model, (0.0, 0.0, 0.0))
    listed = ((usage.prompt_tokens - cached) * price_in + cached * price_cached + usage.completion_tokens * price_out) / 1e6
    return {"calls": 1, "prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "tokens": usage.prompt_tokens + usage.completion_tokens, "cost": float(billed) if billed is not None else listed}


def add_usage(total: dict, more: dict) -> dict:
    for key, value in more.items():
        total[key] = total.get(key, 0) + value
    return total


def clean_findings(raw, most: int) -> list[dict]:
    """Keep only well-formed findings. A malformed one is dropped here rather than cited later."""
    kept = []
    for finding in raw if isinstance(raw, list) else []:
        if not isinstance(finding, dict):
            continue
        quotes = [quote.strip() for quote in finding.get("quotes") or [] if isinstance(quote, str) and quote.strip()]
        if quotes and all(isinstance(finding.get(key), str) and finding[key].strip() for key in ("claim", "source")):
            kept.append({"claim": finding["claim"].strip(), "source": finding["source"].strip(), "quotes": quotes[:MOST_QUOTES]})
    return kept[:most]


async def research(objective: str, budget: int, client, model: str, agent: str,
                   on_event: Callable[[dict], None] = lambda state: None, library: Library | None = None) -> dict:
    """Run one agent to `finish`. Each agent has its own message list: its context is its own window."""
    library = library or Library()
    messages = [{"role": "system", "content": INSTRUCTIONS.format(budget=budget, most=budget)},
                {"role": "user", "content": objective}]
    state = {"agent": agent, "objective": objective, "status": "searching", "queries": [], "reads": [],
             "tool_calls": 0, "findings": [], "usage": {}}
    on_event(state)
    # A model that keeps talking instead of calling tools still ends: the budget counts turns too.
    for _ in range(budget + 3):
        spent = state["tool_calls"] >= budget
        response = await client.chat.completions.create(
            model=model, messages=messages, tools=TOOLS, tool_choice=FINISH_NOW if spent else "auto",
            parallel_tool_calls=False, extra_body={"usage": {"include": True}})
        add_usage(state["usage"], usage_of(response, model))
        said = response.choices[0].message
        if not said.tool_calls:
            messages += [{"role": "assistant", "content": said.content or ""},
                         {"role": "user", "content": "Call finish with your findings now."}]
            state["tool_calls"] += 1
            on_event(state)
            continue
        messages.append({"role": "assistant", "content": said.content, "tool_calls": [
            {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}}
            for call in said.tool_calls]})
        for call in said.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            state["tool_calls"] += 1
            if call.function.name == "search":
                state["queries"].append(args.get("query", ""))
                result = library.search_text(args.get("query", ""))
            elif call.function.name == "read":
                state["reads"].append(args.get("source", ""))
                result = library.read(args.get("source", ""))
            elif call.function.name == "finish":
                state["findings"] = clean_findings(args.get("findings"), budget)
                state["status"] = "done"
                result = f"Received {len(state['findings'])} findings."
            else:
                result = f"No tool {call.function.name!r}. Use search, read or finish."
            # Models judge effort badly, so every result says what is left of the budget.
            left = max(budget - state["tool_calls"], 0)
            # Every tool call gets an answer, even after finish: the API rejects a turn with one missing.
            messages.append({"role": "tool", "tool_call_id": call.id, "content": f"{result}\n\n[{left} tool calls left]"})
        on_event(state)
        if state["status"] == "done":
            return state
    state["status"] = "done"
    on_event(state)
    return state
