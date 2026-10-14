"""Plain RAG: search once with the engineer's own words, then answer.

This is the baseline the agent has to beat. One search finds what the question's words match, which
is often one of the two policies a question needs: the runbook, but not the freeze rule that applies.
"""

from policy.answer import write
from policy.search import Policies

K = 4  # one search, so it gets a little more room than each of the agent's searches


def plain(question: str, policies: Policies, client, model: str) -> dict:
    found = [p.id for p in policies.search(question, K)]
    result, cost = write(question, found, policies, client, model)
    return result | {"sources": found, "searches": [{"query": question, "found": found}], "cost": cost, "calls": 1}
