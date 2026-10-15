"""A scripted model, so the assistant, both servers and the judge are tested with no key and no network."""

import json
from types import SimpleNamespace


def call(tool: str, **args) -> tuple:
    return (tool, args)


class FakeOpenAI:
    """Plays `script` one model turn at a time: a list of tool calls for that step, or a reply string."""

    def __init__(self, script: list, tokens: tuple[int, int] = (1000, 50)):
        self.script, self.tokens, self.turns, self.seen = list(script), tokens, 0, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **request):
        self.turns += 1
        # Kept so a test can read what the model was shown: the tool list and every tool result.
        self.seen.append({**request, "messages": list(request["messages"])})
        turn = self.script.pop(0) if self.script else "Done."
        usage = SimpleNamespace(prompt_tokens=self.tokens[0], completion_tokens=self.tokens[1])
        if isinstance(turn, str):
            message = SimpleNamespace(content=turn, tool_calls=None)
        else:
            message = SimpleNamespace(content=None, tool_calls=[
                SimpleNamespace(id=f"call{self.turns}-{index}",
                                function=SimpleNamespace(name=tool, arguments=json.dumps(args)))
                for index, (tool, args) in enumerate(turn)])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)
