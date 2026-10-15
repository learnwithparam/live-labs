"""A scripted model for the keyless tests: each reply is written down in advance, so a test checks what
the agent's tools do to the booking system without a key, a network or a LiveKit server."""

import json
import uuid

from livekit.agents import APIConnectOptions, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS


def tool_call(name: str, **arguments) -> llm.FunctionToolCall:
    return llm.FunctionToolCall(name=name, arguments=json.dumps(arguments), call_id=uuid.uuid4().hex[:8])


class ScriptedLLM(llm.LLM):
    """Answers each request with the next reply in the script: a sentence, or a list of tool calls."""

    def __init__(self, replies: list):
        super().__init__()
        self.replies, self.requests = list(replies), []

    def chat(self, *, chat_ctx, tools=None, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
             **_unused) -> "ScriptedStream":
        self.requests.append(chat_ctx.copy())
        reply = self.replies.pop(0) if self.replies else "Is there anything else?"
        return ScriptedStream(self, reply, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


class ScriptedStream(llm.LLMStream):
    def __init__(self, model: ScriptedLLM, reply, **options):
        super().__init__(model, **options)
        self.reply = reply

    async def _run(self) -> None:
        # One chunk is enough: the session treats it the way it treats a streamed model reply.
        if isinstance(self.reply, str):
            delta = llm.ChoiceDelta(role="assistant", content=self.reply)
        else:
            delta = llm.ChoiceDelta(role="assistant", tool_calls=self.reply)
        self._event_ch.send_nowait(llm.ChatChunk(id=uuid.uuid4().hex[:8], delta=delta))
