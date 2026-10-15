"""A scripted auditor and a simulated prompt cache, so the policies are costed with no key and no network.

The cache works the way OpenAI's does: the part of a request that matches the start of an earlier one
is served from cache. Tokens are counted as four characters, close enough to compare two policies.
"""

import json
from types import SimpleNamespace

from halyard.books import expected

CHARACTERS_PER_TOKEN = 4


def tokens(text: str) -> int:
    return len(text) // CHARACTERS_PER_TOKEN


class FakeAuditor:
    """Audits like a careful clerk: list, then read and record each invoice in order, then finish."""

    def __init__(self, billed_cost: float | None = None):
        wanted = expected()
        self.plan = [("list_invoices", {})]
        for invoice_id, finding in wanted.items():
            duplicate_of = finding.reason.removeprefix("duplicate of ") if finding.reason.startswith("duplicate") else ""
            self.plan += [("read_invoice", {"invoice": invoice_id}),
                          ("record_finding", {"invoice": invoice_id, "verdict": finding.verdict,
                                              "duplicate_of": duplicate_of, "reason": finding.reason})]
        self.plan.append(("finish", {"summary": "September audited."}))
        self.seen: list[str] = []
        self.requests: list[dict] = []
        self.billed_cost = billed_cost
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def cached(self, request: str) -> int:
        """The longest start this request shares with any earlier one, found by halving."""
        longest = 0
        for earlier in self.seen:
            low, high = 0, min(len(earlier), len(request))
            while low < high:
                middle = (low + high + 1) // 2
                if earlier[:middle] == request[:middle]:
                    low = middle
                else:
                    high = middle - 1
            longest = max(longest, low)
        return tokens(request[:longest])

    def create(self, **request):
        self.requests.append(request)
        # Tools first, then messages: the order the provider reads, and so the order it caches.
        serialized = json.dumps(request["tools"]) + json.dumps(request["messages"])
        cached = self.cached(serialized)
        self.seen.append(serialized)
        tool_name, arguments = self.plan.pop(0) if self.plan else ("finish", {"summary": "done"})
        message = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
            id=f"call{len(self.requests)}", function=SimpleNamespace(name=tool_name, arguments=json.dumps(arguments)))])
        usage = SimpleNamespace(prompt_tokens=tokens(serialized), completion_tokens=40,
                                prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
                                model_extra={"cost": self.billed_cost} if self.billed_cost is not None else {})
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)
