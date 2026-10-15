"""The proxy's hooks, called the way LiteLLM calls them: refuse at the budget, route, book the bill."""

import asyncio
import json

import pytest
from fastapi import HTTPException

from fakes import classifier, success, team_key
from fernway.budgets import Budgets, Ledger
from fernway.hooks import GatewayHooks


def hooks(label: str = "lookup") -> GatewayHooks:
    return GatewayHooks(classify=lambda text, metadata: classifier(label)(text))


def call(model: str, text: str) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": text}], "metadata": {"item": "ask", "run": "test"}}


def test_a_team_at_its_budget_is_refused_and_the_refusal_is_booked():
    with pytest.raises(HTTPException) as refused:
        asyncio.run(hooks().async_pre_call_hook(team_key("growth"), None, call("auto", "Three subject lines"), "completion"))
    assert refused.value.status_code == 402 and "instead of sending it to a cheaper model" in refused.value.detail
    assert [(row["team"], row["status"]) for row in Ledger().rows()] == [("growth", "refused")]


def test_a_raised_budget_lets_the_call_through():
    Budgets().raise_budget("growth", 300.0, "test admin")
    data = asyncio.run(hooks().async_pre_call_hook(team_key("growth"), None, call("auto", "Is it vegan?"), "completion"))
    assert data["model"] == "cheap"


def test_auto_is_replaced_by_the_router_tier_and_an_explicit_tier_is_kept():
    routed = asyncio.run(hooks("judgment").async_pre_call_hook(team_key("care"), None, call("auto", "Refund?"), "completion"))
    assert routed["model"] == "strong" and routed["metadata"]["decision"]["label"] == "judgment"
    pinned = asyncio.run(hooks().async_pre_call_hook(team_key("care"), None, call("cheap", "Refund?"), "completion"))
    assert pinned["model"] == "cheap" and "decision" not in pinned["metadata"]


def test_a_success_books_the_billed_cost_and_the_fallback():
    kwargs, response = success("strong", 0.00005, failed_first="cheap")
    asyncio.run(hooks().async_log_success_event(kwargs, response, None, None))
    row = Ledger().rows()[0]
    assert row["cost"] == 0.00005 and row["billed"] and row["fallback_from"] == "cheap" and row["tier"] == "strong"
    assert Budgets().spent("search") == pytest.approx(412.18 + 0.00005)


def test_a_cost_from_the_price_table_is_marked_as_not_billed():
    kwargs, response = success("cheap", 0.00001, billed=False)
    asyncio.run(hooks().async_log_success_event(kwargs, response, None, None))
    assert Ledger().rows()[0]["billed"] is False


def test_a_provider_error_is_booked_without_the_ssl_noise(fresh_state):
    kwargs = {"litellm_params": {"metadata": {"team": "search", "model_group": "cheap"}}, "model": "qwen",
              "exception": "Cannot connect to host 127.0.0.1:8269 ssl:<ssl.SSLContext object at 0x1> [refused]"}
    asyncio.run(hooks().async_log_failure_event(kwargs, None, None, None))
    row = json.loads((fresh_state / "ledger.jsonl").read_text())
    assert row["status"] == "provider_error" and "ssl" not in row["error"]
