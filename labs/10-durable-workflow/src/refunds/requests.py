"""Fernhill Home's orders, refund policy and this week's refund requests, with the finance team's ruling."""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DATA = Path(__file__).parent.parent.parent / "data"


@dataclass(frozen=True)
class Request:
    id: str
    order: str
    received: str
    message: str
    # What finance ruled the refund should be, in dollars. The model never sees it; the review compares.
    ruling: float


def orders() -> dict[str, dict]:
    return {order["order"]: order for order in json.loads((DATA / "orders.json").read_text())}


def policy() -> str:
    return (DATA / "policy.md").read_text()


def requests() -> list[Request]:
    return [Request(**json.loads(line)) for line in (DATA / "requests.jsonl").read_text().splitlines() if line.strip()]


def request(refund_id: str) -> Request:
    found = [refund_request for refund_request in requests() if refund_request.id == refund_id]
    if not found:
        raise SystemExit(f"No refund request {refund_id}. They are {', '.join(refund_request.id for refund_request in requests())}.")
    return found[0]


def days(count: int) -> str:
    return f"{count} day" if count == 1 else f"{count} days"


def facts(refund_request: Request, order: dict) -> str:
    """The order and the dates, worked out in code. A model that has to count days gets them wrong."""
    received, delivered = date.fromisoformat(refund_request.received), date.fromisoformat(order["delivered"])
    late = (delivered - date.fromisoformat(order["promised"])).days
    return "\n".join([
        f"Order {order['order']}: {order['item']}",
        f"Item price ${order['price']:.2f}, delivery fee ${order['delivery_fee']:.2f}",
        f"Made to order: {'yes' if order['made_to_order'] else 'no'}",
        f"Delivered {days(late)} after the promised date" if late > 0 else "Delivered on time",
        f"Request received {days((received - delivered).days)} after delivery",
        f"Customer's message: {refund_request.message}",
    ])
