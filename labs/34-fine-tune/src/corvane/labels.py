"""The review queue's labels: export them, split them, and turn them into chat examples to train on.

The labels are the truth. A reviewer read the receipt and picked the category, so every number this lab
reports is a comparison with what a person decided, never with what another model said.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path

REVIEWED = Path("data/reviewed.jsonl")
INCOMING = Path("data/incoming.jsonl")
# What reviewers decided on items the models disagreed about. They join the next training set.
DECISIONS = Path("out/decisions.jsonl")
SPLIT_DIR = Path("out/data")
# Kept outside SPLIT_DIR on purpose: the trainer reads every split in that folder, and must never see this one.
HELDOUT = Path("out/heldout.jsonl")

CATEGORIES = ["airfare", "lodging", "ground_transport", "meals", "software", "advertising",
              "office_supplies", "shipping", "telecom", "professional_services"]

# One line per category is all the tuned model is told. The rest it learned from the labels.
SHORT_INSTRUCTIONS = (
    "Classify one Corvane card transaction into a spend category. Reply with the category name only.\n"
    "Categories: " + ", ".join(CATEGORIES) + "."
)

# The written policy, as the large model gets it. The reviewers' unwritten habits are not in here,
# because nobody wrote them down: that gap is what the reviewed labels carry and a prompt does not.
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


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def split_of(item_id: str) -> str:
    """Train, valid or test, from a hash of the id, so an item lands in the same split on every export.

    A random shuffle would move items between train and test on each run, and a test item the model
    once trained on makes the accuracy a lie.
    """
    bucket = int(hashlib.sha256(item_id.encode()).hexdigest(), 16) % 100
    return "test" if bucket < 20 else "valid" if bucket < 30 else "train"


def user_message(item: dict) -> str:
    return f"Descriptor: {item['descriptor']}\nAmount: ${item['amount']:.2f}"


def chat_example(item: dict) -> dict:
    """One training row in the chat format mlx-lm, OpenAI and Hugging Face trainers all read."""
    return {"messages": [{"role": "system", "content": SHORT_INSTRUCTIONS},
                         {"role": "user", "content": user_message(item)},
                         {"role": "assistant", "content": item["label"]}]}


def export(reviewed: list[dict], decisions: list[dict]) -> dict[str, list[dict]]:
    """Split the queue's labels, and add the reviewers' latest decisions to training only.

    New decisions never enter the test set. It stays frozen, so this round's accuracy compares with
    last round's on the same items.
    """
    splits: dict[str, list[dict]] = {"train": [], "valid": [], "test": []}
    for item in reviewed:
        splits[split_of(item["id"])].append(item)
    known = {item["id"] for item in reviewed}
    splits["train"] += [item for item in decisions if item["id"] not in known and item["label"] in CATEGORIES]
    return splits


def write_splits(splits: dict[str, list[dict]], folder: Path = SPLIT_DIR, heldout: Path = HELDOUT) -> None:
    # mlx_lm.lora reads train.jsonl and valid.jsonl from one folder. The test set is kept as raw items.
    write_jsonl(folder / "train.jsonl", [chat_example(item) for item in splits["train"]])
    write_jsonl(folder / "valid.jsonl", [chat_example(item) for item in splits["valid"]])
    write_jsonl(heldout, splits["test"])


def class_counts(items: list[dict]) -> Counter:
    return Counter(item["label"] for item in items)


def parse_label(text: str | None) -> str | None:
    """The category a model answered, or None when the answer is not exactly one category.

    A model that rambles, or names two categories, has not decided. That item goes to a reviewer
    rather than being forced into the nearest guess.
    """
    if not text:
        return None
    cleaned = text.strip().strip(".`'\"").strip().lower().replace(" ", "_")
    if cleaned in CATEGORIES:
        return cleaned
    named = [category for category in CATEGORIES if category in cleaned]
    return named[0] if len(named) == 1 and len(cleaned) <= len(named[0]) + 12 else None
