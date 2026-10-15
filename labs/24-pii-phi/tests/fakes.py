"""A scripted note writer, so the pipeline, the review and the editor are tested with no key and no network."""

from types import SimpleNamespace

from wrenfield.redact import PLACEHOLDER


class FakeOpenAI:
    """Writes a note that repeats every placeholder it was shown, plus `extra` text, and records each call."""

    def __init__(self, extra: str = "", cost: float | None = 0.0002):
        self.extra, self.cost, self.seen = extra, cost, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        # Kept so a test can read exactly what would have crossed the wire.
        self.seen.append(request)
        shown = request["messages"][-1]["content"]
        placeholders = " ".join(dict.fromkeys(match.group() for match in PLACEHOLDER.finditer(shown)))
        note = f"Subjective: seen today. Mentioned {placeholders}.{self.extra}"
        usage = SimpleNamespace(prompt_tokens=900, completion_tokens=120, prompt_tokens_details=None,
                                model_extra={"cost": self.cost} if self.cost is not None else {})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=note))], usage=usage)
