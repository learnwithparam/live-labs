"""The AI step: read a refund request against the policy and decide the amount, citing the rules used.

The model applies the policy; code does everything else. Code works out the dates, caps the amount
at what the customer paid, and decides whether a manager must approve. A rule a model can forget does
not belong in a prompt.
"""

from pydantic import BaseModel, Field, ValidationError

from refunds.requests import Request, facts, orders, policy

# R6 in the policy, enforced here rather than by the model.
APPROVAL_OVER = 500.00

INSTRUCTIONS = """You decide refunds for Fernhill Home, an online furniture store.
Apply the refund policy to the request. Use only the facts given; do not assume anything else.
Give the refund amount in dollars (0 when no refund is due), the rule ids you applied, and one
sentence to the customer that explains the decision."""


class Decision(BaseModel):
    amount: float = Field(ge=0)
    rules: list[str]
    reason: str


def decide(refund_request: Request, client, model: str) -> tuple[dict, float]:
    """One model call. Returns the decision as plain data, ready to be stored in a workflow's history."""
    order = orders()[refund_request.order]
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": f"{INSTRUCTIONS}\n\n{policy()}"}, {"role": "user", "content": facts(refund_request, order)}],
        temperature=0,
        response_format={"type": "json_schema", "json_schema": {"name": "decision", "schema": Decision.model_json_schema()}},
    )
    cost = getattr(response.usage, "cost", None) or 0.0
    try:
        got = Decision.model_validate_json(response.choices[0].message.content or "")
    except ValidationError:
        # No amount we can trust: a manager decides, as with any large refund.
        return {"amount": 0.0, "rules": [], "reason": "The model's reply did not parse.", "needs_approval": True}, cost
    # Never more than the customer paid, whatever the model wrote.
    amount = round(min(got.amount, order["price"] + order["delivery_fee"]), 2)
    return {"amount": amount, "rules": got.rules, "reason": got.reason, "needs_approval": amount > APPROVAL_OVER}, cost
