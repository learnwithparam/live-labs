"""The lab's promise, on real models through a real LiteLLM proxy: routed calls cost less per request
than sending everything to the strong tier, pass at most one fewer labelled item, and every cost is the
one OpenRouter billed. A team at its budget is refused, and a dead cheap tier falls back to strong."""

import pytest

from fernway.budgets import Ledger
from fernway.cli import clients
from fernway.config import llm_settings
from fernway.evaluate import compare, run_mode, wait_for_ledger
from fernway.proxy import running_proxy


@pytest.mark.proof
def test_routing_is_cheaper_per_request_at_the_same_quality_on_the_bill():
    llm_settings()
    with running_proxy() as proxy_url:
        client_for = clients(proxy_url)
        naive, routed = run_mode("strong", client_for), run_mode("routed", client_for)
        with pytest.raises(Exception) as refused:
            client_for("growth").chat.completions.create(model="auto", messages=[{"role": "user", "content": "Hi"}])
    metric = compare(naive, routed)
    print(metric)
    assert naive["all_billed"] and routed["all_billed"]
    assert routed["tiers"]["cheap"] > 0 and routed["tiers"]["strong"] > 0
    assert metric["cheaper"], metric
    assert metric["quality_holds"], metric
    assert getattr(refused.value, "status_code", None) == 402


@pytest.mark.proof
def test_a_dead_cheap_tier_falls_back_to_strong():
    llm_settings()
    with running_proxy(cheap_down=True) as proxy_url:
        reply = clients(proxy_url)("search").chat.completions.create(
            model="cheap", messages=[{"role": "user", "content": "Say ok."}],
            extra_body={"metadata": {"item": "fallback", "run": "proof-fallback"}})
        rows = wait_for_ledger("proof-fallback", 1, Ledger())
    assert reply.choices[0].message.content
    statuses = [(row["status"], row["tier"]) for row in rows]
    assert ("provider_error", "cheap") in statuses
    assert any(row["status"] == "ok" and row["tier"] == "strong" and row["fallback_from"] == "cheap" for row in rows)
