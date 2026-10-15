"""A scripted model, so the agents, the writer and the citation check are tested with no key and no network."""

import asyncio
import json
import re
from types import SimpleNamespace

GRANT_QUOTE = "Berlin reopened its cargo bike grant on 1 April 2026."


def reply(content: str = "", calls: list | None = None, tokens: tuple[int, int] = (1000, 50)):
    """One chat completion in the shape the openai client returns."""
    tool_calls = [SimpleNamespace(id=f"call-{number}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))
                  for number, (name, args) in enumerate(calls or [])] or None
    usage = SimpleNamespace(prompt_tokens=tokens[0], completion_tokens=tokens[1], prompt_tokens_details=None, model_extra={})
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))], usage=usage)


def researcher(request: dict):
    """Search once, read S09, then finish with one quoted finding: what a careful searcher does."""
    done = sum(message["role"] == "tool" for message in request["messages"])
    if done == 0:
        return reply(calls=[("search", {"query": "cargo bike grant Berlin"})])
    if done == 1:
        return reply(calls=[("read", {"source": "S09"})])
    return reply(calls=[("finish", {"findings": [{"claim": "Berlin reopened its grant in April 2026.", "source": "S09", "quotes": [GRANT_QUOTE]}]})])


def writer(request: dict):
    """Cite every finding once, and reword nothing the check reads."""
    keys = re.findall(r"^(F\d+) ", request["messages"][1]["content"], re.MULTILINE)
    claims = [{"text": f"Claim from the finding ({key}).", "finding": key} for key in keys]
    return reply(json.dumps({"title": "Brief", "answer": "Go [F1, F2].", "sections": [{"heading": "Grants", "claims": claims}]}))


def judge(request: dict):
    return reply(json.dumps({"supports": True, "reason": "The quote says it."}))


class FakeModel:
    """Answers by the kind of request, and records how many calls were in flight at once."""

    def __init__(self, plan: list[str] | None = None, research=researcher, check=judge):
        self.plan = plan or ["Find grants.", "Find dealers.", "Find rules."]
        self.research, self.check = research, check
        self.in_flight, self.most_in_flight, self.requests = 0, 0, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **request):
        self.requests.append(request)
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        # A real call takes time; the pause lets other agents start, so overlap shows up if it exists.
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        system = request["messages"][0]["content"]
        if system.startswith("Plan"):
            return reply(json.dumps({"objectives": self.plan}))
        if system.startswith("Write"):
            return writer(request)
        if system.startswith("Decide"):
            return self.check(request)
        return self.research(request)
