"""The large model's prompt, parsing and bill, with a scripted model and no key."""

from fakes import FakeOpenAI

from corvane.classify import LargeModel, few_shot_messages, pick_examples, short_messages, usage_cost
from corvane.labels import CATEGORIES, POLICY, REVIEWED, export, read_jsonl

ITEM = {"id": "TX-1", "descriptor": "UBER *EATS PENDING", "amount": 23.1, "label": "meals"}


def train_and_test() -> tuple[list[dict], list[dict]]:
    parts = export(read_jsonl(REVIEWED), [])
    return parts["train"], parts["test"]


def test_examples_come_from_training_only_two_per_category():
    train, test = train_and_test()
    examples = pick_examples(train)
    assert len(examples) == 2 * len(CATEGORIES)
    assert not {example["id"] for example in examples} & {item["id"] for item in test}


def test_the_few_shot_prompt_carries_the_policy_and_the_examples_as_turns():
    train, _ = train_and_test()
    messages = few_shot_messages(ITEM, pick_examples(train))
    assert POLICY in messages[0]["content"]
    assert len(messages) == 1 + 2 * 2 * len(CATEGORIES) + 1
    assert messages[-1] == {"role": "user", "content": "Descriptor: UBER *EATS PENDING\nAmount: $23.10"}


def test_the_tuned_prompt_has_no_policy_and_no_examples():
    messages = short_messages(ITEM)
    assert len(messages) == 2 and POLICY not in messages[0]["content"]


def test_the_large_model_is_parsed_and_billed_and_a_failed_call_is_no_label():
    items = [ITEM, {**ITEM, "id": "TX-2", "descriptor": "EXPEDIA 7201"}, {**ITEM, "id": "TX-3", "descriptor": "FEDEX OFFICE"}]
    client = FakeOpenAI({"UBER *EATS PENDING": "meals", "EXPEDIA 7201": "airfare or lodging"}, fail_on="FEDEX OFFICE")
    predictions = LargeModel(client, "openai/gpt-6-luna", examples=[]).classify(items)
    assert [prediction.label for prediction in predictions] == ["meals", None, None]
    assert predictions[0].cost == 0.0001 and predictions[2].cost == 0.0
    assert predictions[2].raw == "error: TimeoutError"
    assert all(request["extra_body"] == {"usage": {"include": True}} for request in client.seen)


def test_list_prices_cost_a_call_when_the_provider_sends_no_bill():
    client = FakeOpenAI({}, cost=None)
    response = client.create(messages=[{"role": "user", "content": "Descriptor: X\nAmount: $1.00"}])
    assert usage_cost(response, "openai/gpt-6-luna") == (900 * 0.10 + 12 * 0.50) / 1e6
