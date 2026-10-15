"""The code LiteLLM's proxy runs on every call: who is calling, may they spend, which tier, what it cost.

LiteLLM loads these by name from config.yaml (`custom_auth` and `callbacks`). Its own virtual keys and
team budgets need a Postgres database; these hooks hold the same rules in two small files, so the lab
runs with one process. The routing, the refusal and the books are the same either way.
"""

import json
import re
from collections.abc import Awaitable, Callable

from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth

from fernway.budgets import Budgets, Ledger, state_dir
from fernway.router import CLASSIFY_PROMPT, route

AUTO = "auto"  # the model name a team sends when it wants the gateway to pick


def team_keys() -> dict[str, str]:
    return json.loads((state_dir() / "keys.json").read_text())


async def user_api_key_auth(request, api_key: str) -> UserAPIKeyAuth:
    """One key per team. The key names the team, so a team can never spend from another's budget."""
    for team, key in team_keys().items():
        if api_key == key:
            return UserAPIKeyAuth(api_key=api_key, team_id=team)
    raise HTTPException(status_code=401, detail="Unknown gateway key.")


async def classify_on_cheap_tier(user_text: str, metadata: dict) -> str:
    # Through the proxy's own router, so the classifier call has fallbacks and lands in the ledger.
    from litellm.proxy.proxy_server import llm_router

    # Reasoning off on every tier: when the cheap tier is down, the strong fallback must also answer in a word.
    reply = await llm_router.acompletion(
        model="cheap", max_tokens=10, temperature=0, metadata=metadata, extra_body={"reasoning": {"enabled": False}},
        messages=[{"role": "system", "content": CLASSIFY_PROMPT}, {"role": "user", "content": user_text}])
    return reply.choices[0].message.content or ""


def readable(exception) -> str:
    # Connection errors carry an SSL context's repr, which tells the admin nothing.
    return re.sub(r"\s*ssl:<[^>]*>", "", str(exception))[:300]


def last_user_text(messages: list[dict]) -> str:
    texts = [message.get("content") for message in messages if message.get("role") == "user"]
    return texts[-1] if texts and isinstance(texts[-1], str) else ""


class GatewayHooks(CustomLogger):
    """Budget check and routing before the call; the ledger row after it, success or provider error."""

    def __init__(self, budgets: Callable[[], Budgets] = Budgets,
                 classify: Callable[[str, dict], Awaitable[str]] = classify_on_cheap_tier):
        super().__init__()
        self.budgets, self.classify = budgets, classify

    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type):
        team = user_api_key_dict.team_id
        metadata = data.setdefault("metadata", {})
        tags = {"team": team, "item": metadata.get("item"), "run": metadata.get("run")}
        budgets = self.budgets()
        allowed, why = budgets.allows(team)
        if not allowed:
            # Refused, logged and shown to the caller. A silent downgrade would hide the overspend.
            budgets.ledger.add({**tags, "purpose": "answer", "status": "refused", "asked_for": data.get("model"),
                                "reason": why, "cost": 0.0})
            raise HTTPException(status_code=402, detail=why)
        metadata.update(tags, purpose="answer", asked_for=data.get("model"))
        if data.get("model") == AUTO:
            decision = await route(last_user_text(data.get("messages", [])),
                                   lambda text: self.classify(text, {**tags, "purpose": "classify"}))
            data["model"] = decision.tier
            metadata["decision"] = decision.as_dict()
        return data

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        metadata = (kwargs.get("litellm_params") or {}).get("metadata") or {}
        headers = (getattr(response_obj, "_hidden_params", None) or {}).get("additional_headers") or {}
        usage = getattr(response_obj, "usage", None)
        # `previous_models` is LiteLLM's note of a deployment that failed before this one answered.
        failed = [earlier.get("model_group") for earlier in metadata.get("previous_models") or []]
        decision = metadata.get("decision") or {}
        self.budgets().ledger.add({
            "team": metadata.get("team"), "item": metadata.get("item"), "run": metadata.get("run"),
            "purpose": metadata.get("purpose", "answer"), "status": "ok", "asked_for": metadata.get("asked_for"),
            "tier": metadata.get("model_group"), "model": kwargs.get("model"), "fallback_from": failed[0] if failed else None,
            "call_id": kwargs.get("litellm_call_id"), "cost": kwargs.get("response_cost") or 0.0,
            # True only when the number came from OpenRouter's bill rather than LiteLLM's price table.
            "billed": "llm_provider-x-litellm-response-cost" in headers,
            "prompt_tokens": getattr(usage, "prompt_tokens", None), "completion_tokens": getattr(usage, "completion_tokens", None),
            "route": decision.get("tier"), "reason": decision.get("reason"), "flagged": decision.get("flagged", False),
            "label": decision.get("label")})

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        metadata = (kwargs.get("litellm_params") or {}).get("metadata") or {}
        if not metadata.get("model_group"):
            return  # a refusal from the pre-call hook, already in the ledger
        # A provider error is logged even when a fallback then answers, so outages show up in the books.
        self.budgets().ledger.add({
            "team": metadata.get("team"), "item": metadata.get("item"), "run": metadata.get("run"),
            "purpose": metadata.get("purpose", "answer"), "status": "provider_error", "tier": metadata.get("model_group"),
            "model": kwargs.get("model"), "error": readable(kwargs.get("exception")), "cost": 0.0})


gateway_hooks = GatewayHooks()
