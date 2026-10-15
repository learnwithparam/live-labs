"""Scripted models, so the assistant, the rails and the page are tested with no key and no network.

The rails model answers the three policy questions by keyword. It is not a classifier; it only lets a
test choose which rail fires and check that NeMo routes the verdict to the right place."""

import json
from types import SimpleNamespace

from nemoguardrails.types import LLMResponse

STEERING_WORDS = ("mostly white", "without children", "ignore your")
PLANTED_WORDS = ("AI assistant", "SYSTEM NOTICE", "assistant instructions")


class FakeRailModel:
    """Stands in for the rails model behind NeMo. Every prompt it is shown is kept in `prompts`."""

    def __init__(self, answer_verdict: str = "allow", unreadable: bool = False):
        self.answer_verdict, self.unreadable, self.prompts = answer_verdict, unreadable, []

    @property
    def model_name(self) -> str:
        return "fake/rails"

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def provider_url(self) -> str | None:
        return None

    async def generate_async(self, prompt, *, stop=None, **kwargs) -> LLMResponse:
        text = prompt if isinstance(prompt, str) else "\n".join(str(message.content) for message in prompt)
        self.prompts.append(text)
        if "Should the buyer message be blocked" in text:
            buyer = text.split("Buyer message:", 1)[1]
            return LLMResponse(content="Yes" if any(word in buyer for word in STEERING_WORDS) else "No")
        if self.unreadable:
            return LLMResponse(content="I'm not able to judge that.")
        if "<listing>" in text:
            # The policy names the tags in its own prose, so the listing is after the last opening tag.
            listing = text.split("<listing>")[-1].split("</listing>", 1)[0]
            found = next((word for word in PLANTED_WORDS if word in listing), "")
            return LLMResponse(content=json.dumps({"planted": bool(found), "quote": found}))
        rewrite = "" if self.answer_verdict == "allow" else "The home has three bedrooms."
        return LLMResponse(content=json.dumps({"verdict": self.answer_verdict, "trait": "familial status",
                                               "reason": "The answer says who the home suits.", "rewrite": rewrite}))

    async def stream_async(self, prompt, *, stop=None, **kwargs):
        yield (await self.generate_async(prompt))


class FakeOpenAI:
    """The listing assistant: answers every message with `reply`, flagged or not, and a billed cost."""

    def __init__(self, reply: str = "It has three bedrooms.", flagged: bool = False, content: str | None = None,
                 cost: float | None = 0.0002):
        self.content = content if content is not None else json.dumps({"flagged": flagged, "reply": reply})
        self.cost, self.requests = cost, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **request):
        self.requests.append(request)
        extra = {"cost": self.cost} if self.cost is not None else {}
        usage = SimpleNamespace(prompt_tokens=900, completion_tokens=60, prompt_tokens_details=None, model_extra=extra)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)
