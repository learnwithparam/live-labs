"""A scripted scoring model, so the LLM baseline is tested with no key and no network."""

import json
import re
from types import SimpleNamespace


def reply(content: str, tokens: tuple[int, int] = (2000, 300)):
    """One chat completion in the shape the openai client returns, with OpenRouter's billed cost."""
    usage = SimpleNamespace(prompt_tokens=tokens[0], completion_tokens=tokens[1], prompt_tokens_details=None,
                            model_extra={"cost": 0.0007})
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)


class FakeScorer:
    """Gives every refund the same chance, can skip one id, and can invent one that was never asked."""

    def __init__(self, chance: int = 40, skip: str | None = None, invent: str | None = None):
        self.chance, self.skip, self.invent, self.requests = chance, skip, invent, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **request):
        self.requests.append(request)
        asked = re.findall(r"refund_id=(FM-\d+)", request["messages"][-1]["content"])
        scores = [{"refund_id": refund_id, "fraud_chance": self.chance} for refund_id in asked if refund_id != self.skip]
        if self.invent:
            scores.append({"refund_id": self.invent, "fraud_chance": 99})
        return reply(json.dumps({"scores": scores}))
