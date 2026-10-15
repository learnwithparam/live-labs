"""The same agent in Google ADK: the same tools, the same prompt, the same budget.

ADK runs the loop from `loop.py` for you. It builds each tool's schema from its type hints and
docstring, keeps the history in a session, and stops after `max_llm_calls`. What it does not know is
your cost budget, so a callback before each model call checks it, and one after charges it.
"""

import asyncio
import threading
import warnings
from typing import Callable

from google.adk.agents import LlmAgent
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.run_config import RunConfig
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from kestrel.budget import Budget

# ADK flags its JSON schema tool declarations as experimental on every call; the notice is for ADK
# developers, and it buried the tool calls in the terminal.
warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\]")

HANDED_OFF = "I am passing this to a colleague who will reply within 4 hours."

# Every ADK chat runs on this one event loop, on its own thread. LiteLLM logs each call on a background
# task bound to the first loop it sees; a fresh loop per chat left those tasks orphaned when it closed.
LOOP = asyncio.new_event_loop()
threading.Thread(target=LOOP.run_forever, daemon=True).start()


def openrouter(model: str, base_url: str, key: str, timeout: float = 60) -> LiteLlm:
    # LiteLLM's "openai/" prefix means any OpenAI-compatible endpoint, here OpenRouter.
    return LiteLlm(model=f"openai/{model}", api_base=base_url, api_key=key, timeout=timeout, num_retries=2)


def run(message: str, system: str, tools: list[Callable], llm, budget: Budget,
        hand_off: Callable[[str], dict], on_step: Callable[[dict], None] = lambda step: None) -> dict:
    """Run one chat to a reply or to the edge of its budget. `llm` is any ADK model."""
    stopped = {}

    def before_model(callback_context, llm_request):
        if reason := budget.over(steps=False):
            # Returning a response skips the model call: the cost budget ends the chat here.
            stopped["reason"] = reason
            return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=HANDED_OFF)]))
        return None

    def after_model(callback_context, llm_response):
        usage = llm_response.usage_metadata
        if usage:
            budget.charge(usage.prompt_token_count or 0, usage.candidates_token_count or 0)
            on_step({"kind": "budget", **budget.state()})
        return None

    agent = LlmAgent(name="kestrel_support", model=llm, instruction=system, tools=tools,
                     before_model_callback=before_model, after_model_callback=after_model)

    async def go() -> str:
        sessions = InMemorySessionService()
        # One session per chat: ADK keeps the history there, as `messages` does in our loop.
        await sessions.create_session(app_name="kestrel", user_id="customer", session_id="chat")
        runner = Runner(app_name="kestrel", agent=agent, session_service=sessions)
        reply = ""
        async for event in runner.run_async(user_id="customer", session_id="chat",
                                            new_message=types.Content(role="user", parts=[types.Part(text=message)]),
                                            run_config=RunConfig(max_llm_calls=budget.max_steps)):
            for call in event.get_function_calls():
                on_step({"kind": "call", "tool": call.name, "args": dict(call.args or {})})
            for done in event.get_function_responses():
                on_step({"kind": "result", "tool": done.name, "result": done.response})
            if event.is_final_response() and event.content and event.content.parts:
                reply = "".join(part.text or "" for part in event.content.parts)
        return reply

    try:
        reply = asyncio.run_coroutine_threadsafe(go(), LOOP).result()
    except LlmCallsLimitExceededError:
        # ADK's own step budget. Same outcome as ours: a person gets the chat.
        stopped["reason"] = f"step budget: {budget.steps} model calls"
    if "reason" in stopped:
        hand_off(stopped["reason"])
        on_step({"kind": "handoff", "by": "budget", "reason": stopped["reason"]})
        return {"reply": HANDED_OFF, "stopped": "budget"}
    on_step({"kind": "reply", "text": reply})
    return {"reply": reply, "stopped": "reply"}
