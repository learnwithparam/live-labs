"""A scripted model, so the gateway, the log and the audit desk are tested with no key and no network."""

from types import SimpleNamespace


class FakeOpenAI:
    """Answers every call with a short reply naming the question, and keeps each request it was sent."""

    def __init__(self, fail_on: str | None = None):
        self.fail_on, self.seen = fail_on, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        # Kept so a test can prove a refused call never reached the model.
        self.seen.append(request)
        question = request["messages"][-1]["content"]
        if self.fail_on and self.fail_on in question:
            raise TimeoutError("the provider did not answer")
        usage = SimpleNamespace(prompt_tokens=300, completion_tokens=60, prompt_tokens_details=None,
                                model_extra={"cost": 0.00006})
        message = SimpleNamespace(content=f"Scripted answer to: {question[:40]}")
        return SimpleNamespace(id=f"gen-{len(self.seen)}", model=request["model"], usage=usage,
                               choices=[SimpleNamespace(message=message)])
