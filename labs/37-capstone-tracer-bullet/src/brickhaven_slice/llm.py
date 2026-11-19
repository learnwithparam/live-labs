"""One model call as one span, with the tokens it used on the span.

The attribute names follow the OpenTelemetry GenAI conventions, so any backend that reads them can
chart tokens by step. The call has no retry of its own: the pipeline decides whether to try again,
because only the pipeline knows how much of the 8000 ms is left.
"""

from dataclasses import dataclass

import openai
from opentelemetry.trace import Tracer

from brickhaven_slice.telemetry import Ledger

# Errors a second attempt can fix. A wrong key or a malformed request fails the same way every time.
TRANSIENT = (TimeoutError, ConnectionError, openai.APITimeoutError, openai.APIConnectionError,
             openai.RateLimitError, openai.InternalServerError)


@dataclass
class Model:
    """The client and the model it should call, passed around as one thing."""

    client: object
    name: str


def call_model(model: Model, tracer: Tracer, span_name: str, system: str, user: str, timeout_ms: int,
               tags: dict, ledger: Ledger | None = None) -> str:
    """Ask the model once. The span records the attempt even when it raises, which is what a gap would hide."""
    with tracer.start_as_current_span(span_name, attributes={
            **tags, "gen_ai.operation.name": "chat", "gen_ai.request.model": model.name,
            "brickhaven.timeout_ms": timeout_ms}) as span:
        response = model.client.chat.completions.create(
            model=model.name, timeout=timeout_ms / 1000, max_tokens=400, extra_body={"usage": {"include": True}},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        usage = response.usage
        cost = float((getattr(usage, "model_extra", None) or {}).get("cost") or 0)
        span.set_attributes({"gen_ai.usage.input_tokens": usage.prompt_tokens,
                             "gen_ai.usage.output_tokens": usage.completion_tokens, "brickhaven.cost_dollars": cost})
        if ledger is not None:
            ledger.add(usage.prompt_tokens, usage.completion_tokens, cost)
        return response.choices[0].message.content or ""
