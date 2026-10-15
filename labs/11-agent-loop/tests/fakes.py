"""Scripted models, so the loop, ADK and the budgets are tested with no key and no network."""

import json
from types import SimpleNamespace

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types


def call(tool: str, **args) -> tuple:
    return ("call", tool, args)


class FakeOpenAI:
    """Plays `script` one model turn at a time: a list of calls for that step, or a reply string."""

    def __init__(self, script: list, tokens: tuple[int, int] = (1000, 50)):
        self.script, self.tokens, self.calls = list(script), tokens, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls += 1
        turn = self.script.pop(0) if self.script else self.script_end()
        usage = SimpleNamespace(prompt_tokens=self.tokens[0], completion_tokens=self.tokens[1])
        if isinstance(turn, str):
            message = SimpleNamespace(content=turn, tool_calls=None)
        else:
            message = SimpleNamespace(content=None, tool_calls=[
                SimpleNamespace(id=f"c{self.calls}{index}", function=SimpleNamespace(name=tool_name, arguments=json.dumps(arguments)))
                for index, (_, tool_name, arguments) in enumerate(turn)])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    def script_end(self):
        # Past the script, a confused model keeps looking things up: only a budget stops it.
        return [call("find_orders")]


class FakeADK(BaseLlm):
    """The same script as an ADK model."""

    script: list = []
    tokens: tuple = (1000, 50)
    calls: int = 0

    async def generate_content_async(self, llm_request, stream=False):
        self.calls += 1
        turn = self.script.pop(0) if self.script else [call("find_orders")]
        if isinstance(turn, str):
            parts = [types.Part(text=turn)]
        else:
            parts = [types.Part(function_call=types.FunctionCall(name=tool_name, args=arguments)) for _, tool_name, arguments in turn]
        yield LlmResponse(content=types.Content(role="model", parts=parts),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(
                              prompt_token_count=self.tokens[0], candidates_token_count=self.tokens[1]))
