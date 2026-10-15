"""The golden set and the prompt: the same items, words and examples for the served and the hosted model.

The labels are the truth. A reviewer read each transaction and picked its category, so accuracy here
is agreement with a person, never with another model.
"""

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
GOLDEN = DATA / "golden.jsonl"
EXAMPLES = DATA / "examples.jsonl"

CATEGORIES = ["airfare", "lodging", "ground_transport", "meals", "software", "advertising",
              "office_supplies", "shipping", "telecom", "professional_services"]

INSTRUCTIONS = (
    "Classify one Corvane card transaction into a spend category. Reply with the category name only.\n"
    "Categories: " + ", ".join(CATEGORIES) + "."
)

POLICY = """Corvane spend policy:
- airfare: flights, airline fees such as bags and seats.
- lodging: hotels, Airbnb, booking sites for a stay.
- ground_transport: rides, trains, parking, tolls, fuel, car rental.
- meals: restaurants, coffee, catering, food delivery, including Uber Eats and hotel bars or restaurants.
- software: subscriptions, cloud and API spend such as AWS, Google Cloud, OpenAI and Twilio.
- advertising: paid ads on any platform, and printed marketing such as business cards.
- office_supplies: office goods, electronics, furniture, Amazon marketplace orders.
- shipping: postage, couriers, shipping labels.
- telecom: phone and internet bills, business phone lines, inflight wifi.
- professional_services: contractors, freelancers, lawyers, accountants, company filings."""


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def golden_set(folder: Path = DATA) -> list[dict]:
    return read_jsonl(folder / GOLDEN.name)


def user_message(item: dict) -> str:
    return f"Descriptor: {item['descriptor']}\nAmount: ${item['amount']:.2f}"


def messages_for(item: dict, examples: list[dict]) -> list[dict]:
    """The one prompt both models get: the policy, two reviewed examples per category, then the item.

    The examples come from the training split, so no golden item is ever shown to a model as an answer.
    Every request starts with the same long prefix, which a GPU server can cache once and reuse.
    """
    messages = [{"role": "system", "content": INSTRUCTIONS + "\n\n" + POLICY}]
    for example in examples:
        messages += [{"role": "user", "content": user_message(example)},
                     {"role": "assistant", "content": example["label"]}]
    return messages + [{"role": "user", "content": user_message(item)}]


def parse_label(text: str | None) -> str | None:
    """The category a model answered, or None when the answer is not exactly one category.

    An answer that rambles, names two categories or never arrived has not decided. That item fails
    validation and goes to a reviewer instead of being forced into the nearest guess.
    """
    if not text:
        return None
    cleaned = text.strip().strip(".`'\"").strip().lower().replace(" ", "_")
    if cleaned in CATEGORIES:
        return cleaned
    named = [category for category in CATEGORIES if category in cleaned]
    return named[0] if len(named) == 1 and len(cleaned) <= len(named[0]) + 12 else None
