"""Stand-ins for the parts that need a network: the classifier and LiteLLM's callback arguments."""

from types import SimpleNamespace


def classifier(label: str, calls: list | None = None):
    async def classify(text: str) -> str:
        if calls is not None:
            calls.append(text)
        return label
    return classify


def broken_classifier():
    async def classify(text: str) -> str:
        raise ConnectionError("cheap tier down")
    return classify


def team_key(team: str):
    return SimpleNamespace(team_id=team)


def success(tier: str, cost: float, billed: bool = True, failed_first: str | None = None, **metadata):
    """The two arguments LiteLLM passes to a success callback, cut down to the fields the hooks read."""
    metadata = {"team": "search", "item": "QA-01", "run": "test", "purpose": "answer", "model_group": tier,
                "previous_models": [{"model_group": failed_first}] if failed_first else [], **metadata}
    headers = {"llm_provider-x-litellm-response-cost": str(cost)} if billed else {}
    kwargs = {"litellm_params": {"metadata": metadata}, "model": f"model-for-{tier}", "response_cost": cost,
              "litellm_call_id": "call-1"}
    response = SimpleNamespace(_hidden_params={"additional_headers": headers},
                               usage=SimpleNamespace(prompt_tokens=40, completion_tokens=12))
    return kwargs, response


def result(item: str, passed: bool, cost: float, tier: str, flagged: bool = False) -> dict:
    """One graded call, shaped like a row of an eval file."""
    return {"id": item, "team": "care", "task": "care_reply", "difficulty": "hard", "passed": passed,
            "wrong": [] if passed else ["refund: expected 6.75, got 5.0"], "tier": tier, "reason": "test route",
            "flagged": flagged, "cost": cost, "billed": True, "call_id": f"call-{item}", "reply": "{}", "error": None,
            "model": tier, "fallback_from": None, "status": "ok"}
