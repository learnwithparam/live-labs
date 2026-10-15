"""The LLM router: the routing guide and the ticket in, a queue and a confidence out, as JSON.

Structured output makes the queue one of six names every time. The confidence is different: it is
a number the model wrote, like any other token, so nothing makes 0.9 mean right nine times in ten.
"""

import time
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, create_model

from triage.route import Route
from triage.tickets import Ticket

INSTRUCTIONS = """You route support tickets for Tallyroll, a payroll service for small businesses.
Pick the one queue whose team can solve the ticket, using the routing guide.
Then say how confident you are that this queue is right, from 0 to 1."""


def answer_shape(names: list[str]) -> type[BaseModel]:
    # Literal of the queue names: a provider that supports JSON schema cannot return a seventh queue.
    return create_model("Routing", queue=(Literal[tuple(names)], ...), confidence=(float, Field(ge=0, le=1)))


class LLMRouter:
    workers = 4

    def __init__(self, queues: dict[str, str], client, model: str):
        self.client, self.model = client, model
        self.shape = answer_shape(list(queues))
        self.guide = "\n".join(f"- {name}: {what}" for name, what in queues.items())

    def __call__(self, ticket: Ticket) -> Route:
        began = time.perf_counter()
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": f"{INSTRUCTIONS}\n\nRouting guide:\n{self.guide}"},
                      {"role": "user", "content": ticket.text}],
            temperature=0,
            response_format={"type": "json_schema", "json_schema": {"name": "routing", "schema": self.shape.model_json_schema()}},
        )
        ms = (time.perf_counter() - began) * 1000
        # OpenRouter reports what each call cost; other endpoints leave it out.
        cost = getattr(response.usage, "cost", None) or 0.0
        try:
            got = self.shape.model_validate_json(response.choices[0].message.content or "")
        except ValidationError:
            # A reply that does not parse is the cautious outcome: a person routes it.
            return Route("unparsed", 0.0, ms=ms, cost=cost)
        return Route(got.queue, got.confidence, ms=ms, cost=cost)
