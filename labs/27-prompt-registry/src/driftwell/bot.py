"""Driftwell's support bot. It holds no prompt of its own: each reply asks the prompt source first.

Every reply is traced in Langfuse as a generation linked to the prompt version that wrote it, so a bad
answer in production leads straight to the version, and the version to who changed what.
"""

import time

from driftwell.config import PRICES
from driftwell.prompt_source import PromptSource


def usage_of(response, model: str) -> dict:
    """What the provider reported for one call. The bill is the truth; list prices are the fallback."""
    usage = response.usage
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    # OpenRouter adds the billed cost when asked with usage.include; other providers do not.
    billed = (getattr(usage, "model_extra", None) or {}).get("cost")
    price_in, price_cached, price_out = PRICES.get(model, (0.0, 0.0, 0.0))
    listed = ((usage.prompt_tokens - cached) * price_in + cached * price_cached + usage.completion_tokens * price_out) / 1e6
    return {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "cost": float(billed) if billed is not None else listed, "billed": billed is not None}


class SupportBot:
    def __init__(self, source: PromptSource, llm, model: str, tracer=None):
        self.source, self.llm, self.model, self.tracer = source, llm, model, tracer

    def reply(self, message: str, customer_name: str = "there") -> dict:
        served = self.source.get()
        messages = [{"role": "system", "content": served.fill(customer_name=customer_name)},
                    {"role": "user", "content": message}]
        generation = None
        if self.tracer is not None:
            # `prompt=` is the link: Langfuse shows this generation under the prompt version's metrics.
            # A pinned fallback never came from the registry, so it has no version to link.
            generation = self.tracer.start_observation(
                name="support-reply", as_type="generation", model=self.model, input=messages,
                prompt=served.sdk_prompt if served.source != "pinned fallback" else None,
                metadata={"prompt_source": served.source, "prompt_version": served.version})
        response = self.llm.chat.completions.create(model=self.model, messages=messages, max_tokens=400,
                                                    extra_body={"usage": {"include": True}})
        text = (response.choices[0].message.content or "").strip()
        usage = usage_of(response, self.model)
        trace_id = None
        if generation is not None:
            generation.update(output=text, usage_details={"input": usage["prompt_tokens"],
                                                          "output": usage["completion_tokens"]},
                              cost_details={"total": usage["cost"]})
            generation.end()
            trace_id = generation.trace_id
        return {"reply": text, "version": served.version, "source": served.source, "reason": served.reason,
                "usage": usage, "at": time.time(), "trace_id": trace_id}


def linked_version(client, trace_id: str, wait_seconds: float = 30.0) -> int | None:
    """The prompt version Langfuse recorded on a reply's generation, read back from the server.

    Ingestion is asynchronous, so the generation can take a few seconds to appear after a flush.
    """
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        found = client.api.observations.get_many(trace_id=trace_id, type="GENERATION", fields="core,basic,prompt")
        for observation in found.data:
            version = observation.get("prompt_version") if isinstance(observation, dict) else getattr(observation, "prompt_version", None)
            if version is not None:
                return version
        time.sleep(2)
    return None
