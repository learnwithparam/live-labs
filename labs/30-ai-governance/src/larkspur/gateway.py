"""The one door to the model. Every team's service calls `governed_call`; none of them holds the API key.

Three checks run before a token is spent, and each fails closed: the system must be in the inventory,
the model must be one the inventory lists for it, and a customer-facing system must carry a disclosure.
Every call, refused or answered, becomes an audit record, because an auditor asks about both.
"""

import hashlib
from dataclasses import dataclass

from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as gen_ai

from larkspur.auditlog import CONVERSATION, SUBJECT, SYSTEM, AuditLog, pseudonym
from larkspur.config import PRICES
from larkspur.inventory import System, blockers

PROVIDER = "openrouter"


@dataclass
class Outcome:
    """What the calling service gets back: the text to show, or the refusal, plus the record that was logged."""

    status: str  # answered, refused or failed
    delivered: str | None
    reason: str | None
    record: dict


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
            "cost": float(billed) if billed is not None else listed}


def refusal(systems: dict[str, System], request: dict) -> str | None:
    """The first reason this call may not reach the model, or None. Order matters only for the message."""
    system = systems.get(request["system"])
    if system is None:
        return f"{request['system']} is not in the AI inventory"
    found = blockers(system)
    if found:
        return found[0]
    model = request.get("model") or system.models[0]
    if model not in system.models:
        return f"{model} is not a model the inventory lists for {system.id}"
    if system.version_at(request["at"]) is None:
        return f"{system.id} has no prompt version in effect on {request['at'][:10]}"
    return None


def base_record(request: dict, key: bytes) -> dict:
    """The fields every record carries, named with the OpenTelemetry GenAI conventions where one exists."""
    return {"time": request["at"], CONVERSATION: request["id"], SYSTEM: request["system"],
            SUBJECT: pseudonym(request["subject"], key), gen_ai.GEN_AI_OPERATION_NAME: "chat",
            gen_ai.GEN_AI_PROVIDER_NAME: PROVIDER, gen_ai.GEN_AI_INPUT_MESSAGES: as_messages("user", request["text"])}


def as_messages(role: str, text: str) -> list[dict]:
    """The conventions' message shape: a role and typed parts, so a tracing backend can render the record."""
    return [{"role": role, "parts": [{"type": "text", "content": text}]}]


def governed_call(request: dict, systems: dict[str, System], client, log: AuditLog, key: bytes) -> Outcome:
    """Check, call, disclose, record. The record is written before the text is handed back to the service."""
    reason = refusal(systems, request)
    record = base_record(request, key)
    if reason:
        record.update({"outcome": "refused", "refusal.reason": reason,
                       gen_ai.GEN_AI_REQUEST_MODEL: request.get("model")})
        return Outcome("refused", None, reason, log.append(record))
    system = systems[request["system"]]
    model = request.get("model") or system.models[0]
    version = system.version_at(request["at"])
    messages = [{"role": "system", "content": version.instructions}, {"role": "user", "content": request["text"]}]
    record.update({gen_ai.GEN_AI_AGENT_VERSION: version.version, gen_ai.GEN_AI_REQUEST_MODEL: model,
                   gen_ai.GEN_AI_PROMPT_NAME: f"{system.id}@{version.version}",
                   "prompt.sha256": hashlib.sha256(version.instructions.encode()).hexdigest(),
                   gen_ai.GEN_AI_SYSTEM_INSTRUCTIONS: [{"type": "text", "content": version.instructions}]})
    try:
        response = client.chat.completions.create(model=model, messages=messages,
                                                  extra_body={"usage": {"include": True}})
    except Exception as error:
        # A failed call is logged too: "the assistant said nothing" is an answer an auditor may need.
        record.update({"outcome": "failed", "error.type": type(error).__name__})
        return Outcome("failed", None, f"{type(error).__name__}: {error}", log.append(record))
    reply = (response.choices[0].message.content or "").strip()
    # The disclosure is added here, not in each service, so no team can forget it or word it away.
    delivered = f"{system.disclosure}\n\n{reply}" if system.talks_to_customers else reply
    usage = usage_of(response, model)
    record.update({"outcome": "answered", gen_ai.GEN_AI_RESPONSE_ID: response.id,
                   gen_ai.GEN_AI_RESPONSE_MODEL: response.model,
                   gen_ai.GEN_AI_USAGE_INPUT_TOKENS: usage["prompt_tokens"],
                   gen_ai.GEN_AI_USAGE_OUTPUT_TOKENS: usage["completion_tokens"], "cost.usd": usage["cost"],
                   gen_ai.GEN_AI_OUTPUT_MESSAGES: as_messages("assistant", reply), "delivered.text": delivered,
                   "disclosure.shown": system.talks_to_customers, "talks_to_customers": system.talks_to_customers})
    return Outcome("answered", delivered, None, log.append(record))
