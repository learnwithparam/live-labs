"""The embedding router: find last quarter's tickets most like this one, and let them vote.

It needs no model call and no key, and it answers in milliseconds. It only knows words: a ticket
that says "paycheck" looks like a payroll ticket even when the 401(k) setup is what broke.
"""

import time
from collections import Counter

from triage.route import Route
from triage.tickets import Ticket

# How many past tickets vote. Odd, so two queues rarely tie; small, since each queue has ten examples.
NEIGHBOURS = 5
MODEL = "BAAI/bge-small-en-v1.5"


class FastEmbed:
    """A small ONNX embedding model on the CPU. The first run downloads it (about 70 MB)."""

    def __init__(self, name: str = MODEL):
        # Imported here so the keyless tests, which pass a fake encoder, never load onnxruntime.
        from fastembed import TextEmbedding

        self.model = TextEmbedding(name)

    def __call__(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.model.embed(texts)]


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(left_value * right_value for left_value, right_value in zip(left, right))
    norm = (sum(left_value * left_value for left_value in left) ** 0.5) * (sum(right_value * right_value for right_value in right) ** 0.5)
    return dot / norm if norm else 0.0


class EmbeddingRouter:
    def __init__(self, history: list[Ticket], encode):
        self.history, self.encode = history, encode
        # The history is embedded once; each new ticket then costs one embedding and a few dot products.
        self.vectors = encode([ticket.text for ticket in history])

    def __call__(self, ticket: Ticket) -> Route:
        began = time.perf_counter()
        vector = self.encode([ticket.text])[0]
        nearest = sorted(zip(self.vectors, self.history), key=lambda pair: -cosine(vector, pair[0]))[:NEIGHBOURS]
        votes = Counter(ticket.queue for _, ticket in nearest)
        queue, count = votes.most_common(1)[0]
        # The share of neighbours that agree. 3 of 5 is 0.6: the router is not sure, and says so.
        return Route(queue, count / NEIGHBOURS, {voted_queue: votes_for / NEIGHBOURS for voted_queue, votes_for in votes.items()},
                     ms=(time.perf_counter() - began) * 1000)
