"""The labelled set and its graders: plain code that a cheap model cannot talk its way past."""

from collections import Counter

from fernway.quality import grade, items, messages


def test_the_set_covers_four_teams_with_hard_items_in_each():
    quality_set = items()
    assert len(quality_set) == 29 and len({item["id"] for item in quality_set}) == 29
    hard = Counter(item["team"] for item in quality_set if item["difficulty"] == "hard")
    assert set(hard) == {"search", "substitutions", "care", "catalog"}


def test_money_is_graded_to_the_cent_and_a_fenced_reply_still_parses():
    item = {"expect": {"action": "refund", "refund": 6.75}}
    assert grade(item, '```json\n{"action": "refund", "refund": "6.750"}\n```')["passed"]
    failed = grade(item, '{"action": "refund", "refund": 6.7}')
    assert not failed["passed"] and failed["wrong"] == ["refund: expected 6.75, got 6.7"]


def test_a_list_of_answers_accepts_any_one_and_prose_fails():
    item = {"expect": {"sku": ["S1", "S3"]}}
    assert grade(item, '{"sku": "s3"}')["passed"]
    assert grade(item, "I would send S1.")["wrong"] == ["no JSON object in the reply"]


def test_each_call_carries_the_team_instruction_then_the_facts_and_the_ask():
    item = items()[0]
    system, user = messages(item)
    assert system["role"] == "system" and "JSON" in system["content"]
    assert user["content"].startswith(item["context"]) and user["content"].endswith(item["ask"])
