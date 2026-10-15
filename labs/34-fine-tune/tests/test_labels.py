"""The queue export: a frozen test split, chat examples the trainer reads, and labels a person made."""

import importlib.util
import json
from collections import Counter
from pathlib import Path

from corvane.labels import (CATEGORIES, REVIEWED, SHORT_INSTRUCTIONS, chat_example, export, parse_label, read_jsonl,
                            split_of, write_splits)

LAB = Path(__file__).parent.parent


def reviewed() -> list[dict]:
    return read_jsonl(LAB / REVIEWED)


def test_the_queue_export_is_a_realistic_size_with_every_category():
    rows = reviewed()
    assert 600 <= len(rows) <= 1500
    assert len({row["id"] for row in rows}) == len(rows)
    assert {row["label"] for row in rows} == set(CATEGORIES)
    assert all(row["reviewer"] for row in rows)


def test_the_data_is_exactly_what_the_generator_writes():
    spec = importlib.util.spec_from_file_location("generate", LAB / "data" / "generate.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    from datetime import datetime

    rebuilt = generator.build(1200, seed=34, prefix="TX", start=datetime(2026, 7, 6), labelled=True)
    assert rebuilt == reviewed()


def test_every_item_lands_in_one_split_and_the_same_one_each_time():
    rows = reviewed()
    parts = export(rows, [])
    ids = [item["id"] for part in parts.values() for item in part]
    assert sorted(ids) == sorted(row["id"] for row in rows)
    assert all(split_of(row["id"]) == split_of(row["id"]) for row in rows)
    shares = {name: len(part) / len(rows) for name, part in parts.items()}
    assert 0.6 < shares["train"] < 0.8 and 0.15 < shares["test"] < 0.25
    assert set(class_names(parts["test"])) == set(CATEGORIES)


def class_names(items: list[dict]) -> Counter:
    return Counter(item["label"] for item in items)


def test_review_decisions_join_training_and_never_the_test_split():
    rows = reviewed()
    decision = {"id": "NX-00007", "descriptor": "EXPEDIA 7201", "amount": 410.0, "posted": "2026-10-01",
                "label": "airfare", "reviewer": "you"}
    unknown = {**decision, "id": "NX-00008", "label": "groceries"}
    before, after = export(rows, []), export(rows, [decision, unknown])
    assert after["test"] == before["test"]
    assert after["train"] == before["train"] + [decision]


def test_a_chat_example_trains_only_the_category_after_the_short_instructions():
    example = chat_example({"descriptor": "UBER *EATS PENDING", "amount": 23.1, "label": "meals"})
    roles = [message["role"] for message in example["messages"]]
    assert roles == ["system", "user", "assistant"]
    assert example["messages"][0]["content"] == SHORT_INSTRUCTIONS
    assert example["messages"][1]["content"] == "Descriptor: UBER *EATS PENDING\nAmount: $23.10"
    assert example["messages"][2]["content"] == "meals"


def test_the_trainer_folder_never_holds_the_test_split(tmp_path):
    parts = export(reviewed(), [])
    write_splits(parts, tmp_path / "data", tmp_path / "heldout.jsonl")
    assert sorted(path.name for path in (tmp_path / "data").iterdir()) == ["train.jsonl", "valid.jsonl"]
    trained = {json.loads(line)["messages"][1]["content"] for line in (tmp_path / "data" / "train.jsonl").open()}
    assert len(read_jsonl(tmp_path / "heldout.jsonl")) == len(parts["test"])
    assert trained


def test_only_one_clear_category_parses():
    assert parse_label("meals") == "meals"
    assert parse_label(" Ground transport.\n") == "ground_transport"
    assert parse_label("Category: software") == "software"
    assert parse_label("meals or lodging") is None
    assert parse_label("This looks like a hotel stay, so I would say lodging") is None
    assert parse_label("") is None and parse_label(None) is None
