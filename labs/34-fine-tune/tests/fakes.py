"""A scripted large model, so the prompt, the parsing, the cost and the routing are tested with no key."""

from types import SimpleNamespace


class FakeOpenAI:
    """Answers from `answers` (descriptor to reply), records each request, and bills a fixed cost."""

    def __init__(self, answers: dict[str, str], cost: float | None = 0.0001, fail_on: str | None = None):
        self.answers, self.cost, self.fail_on, self.seen = answers, cost, fail_on, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        # Kept so a test can read exactly what would have crossed the wire.
        self.seen.append(request)
        asked = request["messages"][-1]["content"]
        descriptor = asked.splitlines()[0].removeprefix("Descriptor: ")
        if descriptor == self.fail_on:
            raise TimeoutError("scripted timeout")
        usage = SimpleNamespace(prompt_tokens=900, completion_tokens=12, prompt_tokens_details=None,
                                model_extra={"cost": self.cost} if self.cost is not None else {})
        message = SimpleNamespace(content=self.answers.get(descriptor, "meals"))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def prediction(item_id: str, label: str | None, confidence: float | None = 0.99, cost: float = 0.00002,
               raw: str | None = None) -> dict:
    return {"id": item_id, "label": label, "raw": raw if raw is not None else (label or "I am not sure"),
            "confidence": confidence, "cost": cost}
