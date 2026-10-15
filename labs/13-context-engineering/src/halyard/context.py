"""What the model is sent on each step of a long audit, built two ways.

An agent sends its whole context on every step: the tools, the system prompt and the history so far.
The provider keeps the longest start of a request it has seen recently and bills those tokens at a
quarter of the price (OpenAI) or a tenth (Anthropic). So the cheapest context is not the shortest. It
is the one whose start never changes. Manus names the cache hit rate as the most important number for
a production agent.

`NAIVE` does what feels thrifty, and each habit moves the start of the request:

- a clock and a progress line at the top of the system prompt
- a sliding window that drops old turns to keep the request short
- a tool list that grows when `finish` becomes allowed

`ENGINEERED` keeps the start fixed, only ever appends, and says where the audit stands at the end of
the newest tool result, where the model reads it last.
"""

from dataclasses import dataclass, replace
from datetime import datetime

from halyard.books import contract, contract_text

INSTRUCTIONS = """Audit carrier invoices for Halyard Freight against the road contract below.

Each invoice comes beside the rate engine's contract price for the same shipment. Approve it when the
carrier's total is within 0.50 EUR of the contract total. Otherwise dispute it and name the line that
differs and the contract clause it breaks. A second invoice for a shipment you have already seen is a
duplicate: dispute it whatever its lines say. Record one finding per invoice, in invoice order, one at a
time. When every invoice has a finding, call finish.
"""


@dataclass(frozen=True)
class Rules:
    clock: bool = False  # the current time at the top of the system prompt
    progress: bool = False  # "7 of 16 recorded" at the top of the system prompt
    window: int | None = None  # keep only the newest turns
    hide_finish: bool = False  # offer `finish` only once every invoice has a finding
    recite: bool = False  # say where the audit stands at the end of each recorded finding


NAIVE = Rules(clock=True, progress=True, window=6, hide_finish=True)
ENGINEERED = Rules(recite=True)
POLICIES = {"naive": NAIVE, "engineered": ENGINEERED}
BREAKS = ("clock", "progress", "window", "hide_finish")


def with_breaks(rules: Rules, breaks: list[str]) -> Rules:
    """The engineered rules with chosen naive habits put back, so each one's cost can be measured alone."""
    return replace(rules, **{habit: NAIVE.window if habit == "window" else True for habit in breaks})


def system_prompt(rules: Rules, recorded: int, total: int, now: datetime) -> str:
    # Anything above the contract that changes between steps makes every token after it a cache miss.
    head = []
    if rules.clock:
        head.append(f"Current time: {now:%Y-%m-%d %H:%M:%S}")
    if rules.progress:
        head.append(f"Progress: {recorded} of {total} invoices recorded.")
    return "\n".join(head + [INSTRUCTIONS, contract_text(contract())])


def windowed(turns: list[list[dict]], window: int | None) -> list[dict]:
    """The history as messages. A window keeps the task and the newest turns whole.

    A turn is the model's message with the tool results that answer it. Cutting inside a turn would
    leave a tool result with no call, which the API rejects.
    """
    kept = turns if window is None or len(turns) <= window + 1 else turns[:1] + turns[-window:]
    return [message for turn in kept for message in turn]


def build(rules: Rules, tools: list[dict], turns: list[list[dict]], recorded: int, total: int,
          now: datetime) -> dict:
    """The request for the next step: tools first, then messages, which is the order the provider caches in."""
    # Removing a tool rewrites the start of the request. Engineered keeps `finish` and lets it refuse instead.
    offered = [tool for tool in tools if not (rules.hide_finish and tool["function"]["name"] == "finish"
                                              and recorded < total)]
    messages = [{"role": "system", "content": system_prompt(rules, recorded, total, now)}]
    return {"tools": offered, "messages": messages + windowed(turns, rules.window)}


def first_change(previous: dict | None, request: dict) -> str:
    """Where this request stops matching the last one: the point the cache can no longer reach."""
    if previous is None:
        return "first request"
    if previous["tools"] != request["tools"]:
        return "tool list"
    for position, (before, now) in enumerate(zip(previous["messages"], request["messages"])):
        if before != now:
            return "system prompt" if position == 0 else f"history at message {position}"
    # Every earlier message is still there, in place: only new turns were added at the end.
    return "" if len(request["messages"]) >= len(previous["messages"]) else "history trimmed"

