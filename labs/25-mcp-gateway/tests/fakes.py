"""A scripted model, so the agent, the gateway and the review are tested with no key and no network."""

import json
from types import SimpleNamespace


class FakeOpenAI:
    """Plays `script` one model turn at a time: a list of (tool, arguments) calls for that step, or a reply."""

    def __init__(self, script: list):
        self.script, self.turns, self.seen = list(script), 0, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **request):
        self.turns += 1
        # Kept so a test can read which tools the model was offered.
        self.seen.append({**request, "messages": list(request["messages"])})
        turn = self.script.pop(0) if self.script else "Done."
        usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=50, prompt_tokens_details=None,
                                model_extra={"cost": 0.0001})
        if isinstance(turn, str):
            message = SimpleNamespace(content=turn, tool_calls=None)
        else:
            message = SimpleNamespace(content=None, tool_calls=[
                SimpleNamespace(id=f"call{self.turns}-{index}",
                                function=SimpleNamespace(name=tool, arguments=json.dumps(arguments)))
                for index, (tool, arguments) in enumerate(turn)])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    def offered(self, turn: int = 0) -> list[str]:
        return [tool["function"]["name"] for tool in self.seen[turn]["tools"] or []]
