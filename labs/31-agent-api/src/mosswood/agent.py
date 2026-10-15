"""Mosswood's agent API: one FastAPI service that answers a question about a workspace.

Two endpoints answer the same question. `/v1/answer` is the first version every team ships: wait for
the whole answer, return one JSON body. `/v1/chat` streams Server-Sent Events, so the first words reach
the screen while the model is still writing. The agent sits behind the gateway and trusts only the
identity headers the gateway sets after it has checked the token.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import openai
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sse_starlette import EventSourceResponse

from mosswood.config import FIRST_TOKEN_SECONDS, IDLE_SECONDS, OUT, PRICES
from mosswood.workspace import TOOLS, call_tool, tenant, today, workspaces

LEDGER = OUT / "agent.jsonl"
MAX_TOOL_ROUNDS = 4  # one or two lookups answer every question here; the rest is room for a retry
INSTRUCTIONS = (
    "Answer questions about the projects and tasks in this Mosswood workspace. Look things up with the "
    "tools and never guess a task, an owner or a date. Name each task by its id. Keep the answer under "
    "120 words unless asked for more. Today is {today}. The person asking is {user}."
)


def usage_of(usage, model: str) -> dict:
    """What the provider reported for one call. The bill is the truth; list prices are the fallback."""
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "billed": False}
    # OpenRouter adds the billed cost when asked with usage.include; other providers do not.
    billed = (getattr(usage, "model_extra", None) or {}).get("cost")
    price_in, _, price_out = PRICES.get(model, (0.0, 0.0, 0.0))
    listed = (usage.prompt_tokens * price_in + usage.completion_tokens * price_out) / 1e6
    return {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "cost": float(billed) if billed is not None else listed, "billed": billed is not None}


class Ledger:
    """One JSON line per request: how it ended, how fast it started, what it cost. Joined to the gateway's
    log by the request id Envoy puts on every request."""

    def __init__(self, path: Path = LEDGER):
        self.path = path

    def write(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as ledger:
            ledger.write(json.dumps(record) + "\n")

    def rows(self) -> list[dict]:
        return [json.loads(line) for line in self.path.read_text().splitlines()] if self.path.exists() else []


async def next_chunk(stream, deadline: float):
    """Wait for the model's next chunk, but never past the deadline. A stuck provider becomes an error
    event the person can read, instead of a spinner that never stops."""
    return await asyncio.wait_for(stream.__anext__(), max(deadline - time.monotonic(), 0.01))


async def answer_events(client, model: str, tenant_id: str, user_id: str, question: str, ledger: Ledger,
                        request_id: str) -> AsyncIterator[dict]:
    """Run the tool loop and yield each event as it happens: `tool`, `token`, then `done` or `error`."""
    started = time.monotonic()
    record = {"request_id": request_id, "tenant": tenant_id, "user": user_id, "model": model, "status": "streaming",
              "first_token_ms": None, "chunks": 0, "tool_calls": [], "prompt_tokens": 0, "completion_tokens": 0,
              "cost": 0.0, "generation_ids": [], "at": time.time()}
    name = tenant(tenant_id)["users"].get(user_id, {}).get("name", user_id)
    messages = [{"role": "system", "content": INSTRUCTIONS.format(today=today(), user=name)},
                {"role": "user", "content": question}]
    try:
        for _ in range(MAX_TOOL_ROUNDS):
            calls: dict[int, dict] = {}
            # Retries are off in the client: under load a retry doubles the traffic on a provider that
            # is already refusing, so a failure is reported at once and the person can ask again.
            stream = await client.chat.completions.create(
                model=model, messages=messages, tools=TOOLS, stream=True, temperature=0,
                stream_options={"include_usage": True}, extra_body={"usage": {"include": True}})
            async with stream:
                while True:
                    deadline = started + FIRST_TOKEN_SECONDS if record["first_token_ms"] is None else time.monotonic() + IDLE_SECONDS
                    try:
                        chunk = await next_chunk(stream, deadline)
                    except StopAsyncIteration:
                        break
                    if chunk.id and chunk.id not in record["generation_ids"]:
                        record["generation_ids"].append(chunk.id)
                    if chunk.usage:
                        usage = usage_of(chunk.usage, model)
                        for key in ("prompt_tokens", "completion_tokens", "cost"):
                            record[key] += usage[key]
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        if record["first_token_ms"] is None:
                            record["first_token_ms"] = round((time.monotonic() - started) * 1000)
                        record["chunks"] += 1
                        yield {"event": "token", "data": {"text": delta.content}}
                    for part in delta.tool_calls or []:
                        call = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
                        call["id"] = part.id or call["id"]
                        call["name"] += (part.function.name or "") if part.function else ""
                        call["arguments"] += (part.function.arguments or "") if part.function else ""
            if not calls:
                record["status"] = "done"
                yield {"event": "done", "data": {key: record[key] for key in
                                                  ("first_token_ms", "prompt_tokens", "completion_tokens", "cost")}}
                return
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"] or "{}"}}
                for call in calls.values()]})
            for call in calls.values():
                try:
                    arguments = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                record["tool_calls"].append({"name": call["name"], "arguments": arguments})
                yield {"event": "tool", "data": {"name": call["name"], "arguments": arguments}}
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": call_tool(tenant_id, call["name"], arguments)})
        record["status"] = "error"
        record["error"] = "too many tool rounds"
        yield {"event": "error", "data": {"message": "The assistant could not finish this one. Try a narrower question."}}
    except asyncio.CancelledError:
        # The person closed the tab or hung up. Leaving `async with stream` above closed the connection to
        # the provider, which stops generation, so the tokens after this point are never billed.
        record["status"] = "cancelled"
        raise
    except TimeoutError:
        record["status"], record["error"] = "error", "model timed out"
        waited = FIRST_TOKEN_SECONDS if record["first_token_ms"] is None else IDLE_SECONDS
        yield {"event": "error", "data": {"message": f"The model went quiet for {waited} s, so the answer was stopped. Try again."}}
    except openai.APIError as error:
        record["status"], record["error"] = "error", f"{type(error).__name__}: {error}"[:300]
        # The person sees what to do next; the provider's own message stays in the ledger for the operator.
        busy = isinstance(error, openai.RateLimitError)
        yield {"event": "error", "data": {"message": "The assistant is busy right now. Try again in a few seconds."
                                          if busy else "The assistant could not reach its model. Try again."}}
    finally:
        record["total_ms"] = round((time.monotonic() - started) * 1000)
        ledger.write(record)


async def held_until_first_word(events: AsyncIterator[dict]) -> list[dict]:
    """Read events up to the first word of the answer, an error or the end.

    The response headers go out with that first word. So the gateway's "time to first byte" in its log
    is the time to first token, and a model that fails before saying anything gets a 502 the gateway
    counts as an error, not a 200 that hides one.
    """
    held = []
    async for event in events:
        held.append(event)
        if event["event"] in ("token", "error", "done"):
            break
    return held


async def replay(held: list[dict], events: AsyncIterator[dict]) -> AsyncIterator[dict]:
    try:
        for event in held:
            yield {"event": event["event"], "data": json.dumps(event["data"])}
        async for event in events:
            yield {"event": event["event"], "data": json.dumps(event["data"])}
    finally:
        await events.aclose()


def create_app(client_factory: Callable[[], object], model: str, ledger: Ledger | None = None) -> FastAPI:
    app = FastAPI(title="Mosswood agent API")
    ledger = ledger or Ledger()
    client = client_factory()

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "model": model}

    @app.post("/v1/chat")
    async def chat(request: Request):
        # Envoy sets these from the verified token and drops any the caller sent. A request without them
        # did not come through the gateway, so it gets nothing.
        tenant_id, user_id = request.headers.get("x-tenant-id", ""), request.headers.get("x-user-id", "")
        if tenant_id not in workspaces()["tenants"] or not user_id:
            return JSONResponse({"error": "Sign in through the Mosswood app."}, status_code=401)
        question = str((await request.json()).get("question", "")).strip()[:2000]
        if not question:
            return JSONResponse({"error": "Ask a question."}, status_code=400)
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        events = answer_events(client, model, tenant_id, user_id, question, ledger, request_id)
        held = await held_until_first_word(events)
        status = 502 if held and held[-1]["event"] == "error" else 200
        return EventSourceResponse(replay(held, events), status_code=status, ping=10,
                                   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/v1/answer")
    async def answer(request: Request):
        """The naive version: no auth, the tenant from the body, and nothing on screen until the end."""
        body = await request.json()
        tenant_id, user_id = body.get("tenant", ""), body.get("user", "")
        if tenant_id not in workspaces()["tenants"]:
            return JSONResponse({"error": "Unknown workspace."}, status_code=400)
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        text, tools, finish = [], [], {}
        async for event in answer_events(client, model, tenant_id, user_id, str(body.get("question", "")), ledger, request_id):
            if event["event"] == "token":
                text.append(event["data"]["text"])
            elif event["event"] == "tool":
                tools.append(event["data"])
            else:
                finish = event
        if finish.get("event") == "error":
            return JSONResponse({"error": finish["data"]["message"]}, status_code=502)
        return {"answer": "".join(text), "tools": tools, **finish.get("data", {})}

    return app
