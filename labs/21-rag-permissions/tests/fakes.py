"""A scripted model and a fixed access table, so retrieval, filtering and scoring run with no key and no server.

The access table is only for keyless tests of where the check sits. Whether the access lists really
deny and allow the right people is tested against a real OpenFGA server in `test_openfga.py`.
"""

import re
from types import SimpleNamespace


class FakeOpenAI:
    """Answers with `reply(messages)`; by default it repeats every source it was shown, the worst case."""

    def __init__(self, reply=None, tokens: tuple[int, int] = (800, 40)):
        self.reply = reply or (lambda messages: messages[-1]["content"])
        self.tokens, self.seen = tokens, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **request):
        self.seen.append(request)
        usage = SimpleNamespace(prompt_tokens=self.tokens[0], completion_tokens=self.tokens[1],
                                prompt_tokens_details=None, model_extra={})
        message = SimpleNamespace(content=self.reply(request["messages"]))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def cite_everything(messages: list[dict]) -> str:
    """A model that cites every source id it was given, so a test can see exactly what reached it."""
    shown = re.findall(r"^\[([A-Za-z0-9-]+)\]", messages[-1]["content"], re.M)
    return " ".join(f"[{document}]" for document in shown) or "No document you can open answers this."


class FakeAuthorizer:
    """Who may view what, written out by hand: {user: {document ids}}."""

    def __init__(self, table: dict[str, set[str]]):
        self.table, self.calls = table, []

    def allowed(self, user: str, document_ids: list[str]) -> set[str]:
        self.calls.append(("allowed", user, sorted(set(document_ids))))
        return set(document_ids) & self.table.get(user, set())

    def viewable(self, user: str) -> set[str]:
        self.calls.append(("viewable", user))
        return set(self.table.get(user, set()))

    def why(self, user: str, document_id: str) -> list[dict]:
        return [{"subject": f"user:{user}", "relation": "viewer", "object": f"document:{document_id}"}]
