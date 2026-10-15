"""A stand-in for the model provider, speaking the same streaming chat completions API.

A load test sends hundreds of requests in a minute. On a real model that is a real bill, and the
numbers would move with the provider's mood that day. This stand-in streams at a fixed pace, refuses
past a fixed number of open streams the way a provider's rate limit does, and counts every word it
sent, so a cancelled answer shows exactly where generation stopped.
"""

import asyncio
import json
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from mosswood.config import STUB_MODEL

CAPACITY = 16  # open streams the provider allows this account at once; the next one gets a 429
TOOL_DELAY = 0.20  # seconds to decide on a tool call
FIRST_WORD_DELAY = 0.25  # seconds before the first word of the answer
WORD_DELAY = 0.025  # seconds between words: about 40 words a second, a typical small model
LONG_WORDS = 400  # a "full report" question gets this many words, so a hang-up has something to stop


class Provider:
    """Counts what the stand-in did, so a test can read where each stream stopped."""

    def __init__(self, capacity: int = CAPACITY, word_delay: float = WORD_DELAY, fail: bool = False):
        self.capacity, self.word_delay, self.fail = capacity, word_delay, fail
        self.open_streams = 0
        self.stats = {"streams": 0, "refused": 0, "finished": 0, "cut_off": 0, "words_sent": 0, "cut_off_words": []}

    def chunk(self, completion_id: str, delta: dict, finish: str | None = None, usage: dict | None = None) -> bytes:
        body = {"id": completion_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": STUB_MODEL,
                "choices": [] if usage else [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage:
            body["usage"] = usage
        return f"data: {json.dumps(body)}\n\n".encode()

    async def tool_turn(self, completion_id: str):
        await asyncio.sleep(TOOL_DELAY)
        call = {"index": 0, "id": f"call_{completion_id[-6:]}", "type": "function",
                "function": {"name": "search_tasks", "arguments": json.dumps({"overdue_only": True})}}
        yield self.chunk(completion_id, {"role": "assistant", "tool_calls": [call]})
        yield self.chunk(completion_id, {}, finish="tool_calls")
        yield self.chunk(completion_id, {}, usage={"prompt_tokens": 420, "completion_tokens": 18, "total_tokens": 438, "cost": 0.0})
        yield b"data: [DONE]\n\n"

    async def answer_turn(self, completion_id: str, words: list[str]):
        sent = 0
        # Counted here, not when the request arrives: a caller who leaves before the stream starts never
        # runs this generator, and a count taken earlier would never come back down.
        self.open_streams += 1
        try:
            await asyncio.sleep(FIRST_WORD_DELAY)
            for word in words:
                yield self.chunk(completion_id, {"content": word + " "})
                sent += 1
                self.stats["words_sent"] += 1
                await asyncio.sleep(self.word_delay)
            yield self.chunk(completion_id, {}, finish="stop")
            yield self.chunk(completion_id, {}, usage={"prompt_tokens": 610, "completion_tokens": sent, "total_tokens": 610 + sent, "cost": 0.0})
            yield b"data: [DONE]\n\n"
            self.stats["finished"] += 1
        finally:
            # Reached when the caller closes the connection mid-answer: generation stops at `sent` words.
            if sent < len(words):
                self.stats["cut_off"] += 1
                self.stats["cut_off_words"].append(sent)
            self.open_streams -= 1

    def answer_words(self, messages: list[dict]) -> list[str]:
        """Write the answer from what the tool returned, so the words name real task ids."""
        question = next((message["content"] for message in messages if message["role"] == "user"), "")
        found = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
        tasks = [task for result in found if isinstance(result, list) for task in result]
        lines = [f"{task['id']} {task['title']} was due {task['due']}." for task in tasks] or ["Nothing is overdue."]
        words = " ".join(["Here is what is overdue:", *lines]).split()
        if "report" in question.lower():
            words = (words * (LONG_WORDS // max(len(words), 1) + 1))[:LONG_WORDS]
        return words

    async def completions(self, request: Request):
        body = await request.json()
        if self.fail:
            return JSONResponse({"error": {"message": "upstream model overloaded", "code": 500}}, status_code=500)
        if self.open_streams >= self.capacity:
            self.stats["refused"] += 1
            return JSONResponse({"error": {"message": "Rate limit reached for this account", "code": 429}}, status_code=429)
        self.stats["streams"] += 1
        completion_id = f"gen-stand-in-{self.stats['streams']:06d}"
        messages = body.get("messages", [])
        if body.get("tools") and not any(message["role"] == "tool" for message in messages):
            return StreamingResponse(self.tool_turn(completion_id), media_type="text/event-stream")
        return StreamingResponse(self.answer_turn(completion_id, self.answer_words(messages)), media_type="text/event-stream")

    async def read_stats(self, request: Request):
        return JSONResponse({**self.stats, "open_streams": self.open_streams})


def create_stub(provider: Provider | None = None) -> Starlette:
    provider = provider or Provider()
    app = Starlette(routes=[Route("/v1/chat/completions", provider.completions, methods=["POST"]),
                            Route("/stats", provider.read_stats)])
    app.state.provider = provider
    return app
