"""A scripted stand-in for the OpenAI-compatible endpoint, so the pipeline's tool loop runs with no key.

It streams the same chunk shapes the real API does: tool calls arrive as deltas, text arrives word by
word, and the last chunk carries usage with OpenRouter's billed cost.
"""

import json

from openai.types.chat import ChatCompletionChunk


def chunk(delta: dict, finish: str | None = None, usage: dict | None = None) -> ChatCompletionChunk:
    return ChatCompletionChunk.model_validate({
        "id": "fake", "object": "chat.completion.chunk", "created": 0, "model": "fake",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if delta is not None else [],
        "usage": usage,
    })


class FakeStream:
    def __init__(self, chunks: list[ChatCompletionChunk]):
        self.chunks = chunks

    async def stream(self):
        for each_chunk in self.chunks:
            yield each_chunk

    def __aiter__(self):
        return self.stream()

    async def close(self) -> None:
        pass


class FakeCompletions:
    def __init__(self, replies: list[dict]):
        self.replies, self.requests = list(replies), []

    async def create(self, **params) -> FakeStream:
        self.requests.append(params)
        reply = self.replies.pop(0)
        usage = {"prompt_tokens": 400, "completion_tokens": 20, "total_tokens": 420, "cost": 0.00005}
        chunks = []
        for position, (name, arguments) in enumerate(reply.get("tools", [])):
            chunks.append(chunk({"tool_calls": [{"index": position, "id": f"call_{name}_{position}", "type": "function",
                                                 "function": {"name": name, "arguments": json.dumps(arguments)}}]}))
        for word in reply.get("text", "").split(" ") if reply.get("text") else []:
            chunks.append(chunk({"content": word + " "}))
        chunks.append(chunk({}, finish="tool_calls" if reply.get("tools") else "stop"))
        chunks.append(chunk(None, usage=usage))
        return FakeStream(chunks)


class FakeClient:
    def __init__(self, replies: list[dict]):
        self.completions = FakeCompletions(replies)
        self.chat = self

    async def close(self) -> None:
        pass
