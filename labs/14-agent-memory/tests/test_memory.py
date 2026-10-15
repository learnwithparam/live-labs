"""Both memory policies on real Mem0 and a real Qdrant store on disk, with a scripted model and a
word-count embedder, so every promise is checked with no key and no network."""

import json
import re
import threading
import urllib.error
import urllib.request

import pytest
from fakes import FakeOpenAI, WordEmbedder

from pawsley.agent import chat, recall
from pawsley.keeper import edit, forget, settle, WRITERS
from pawsley.memory import BilledModel, all_memories, open_memory
from pawsley.review import customer, customers, says, score, verdict
from pawsley.serve import Desk, serve

MODEL = "openai/gpt-6-luna"
MOVED = "We've just moved. Please send boxes to 3 Harbour Road, Falmouth from now on."
FORGET = "Please forget my phone number. I don't want texts any more. Email me at hannah.weiss@fastmail.com instead."
TREATS = "Can you add the chicken jerky treats to the next box? Biscuit has been fine with them at the groomer's."


def open_store(folder, fake: FakeOpenAI):
    billed = BilledModel(fake, MODEL)
    return open_memory(folder, billed, WordEmbedder(), fresh=True), billed


def tell(memory, billed, policy: str, customer_id: str, messages: list[str]) -> list[dict]:
    events = []
    for message in messages:
        events += WRITERS[policy](memory, billed, customer_id, message)
    return events


def texts(memory, customer_id: str) -> list[str]:
    return [item["memory"] for item in all_memories(memory, customer_id)]


def test_append_only_keeps_the_old_address_beside_the_new_one(tmp_path):
    memory, billed = open_store(tmp_path, FakeOpenAI())
    tell(memory, billed, "append-only", "C-101", [*customer("C-101")["sessions"][0], MOVED])
    saved = texts(memory, "C-101")
    assert any("Mill Lane" in text for text in saved) and any("Harbour Road" in text for text in saved)
    assert len(score(memory, customer("C-101"))["wrong"]) == 1


def test_kept_replaces_the_old_address_and_mem0_history_shows_what_it_was(tmp_path):
    fake = FakeOpenAI(decisions={MOVED: [{"do": "replace", "old": "Mill Lane", "new": "Harbour Road"}]})
    memory, billed = open_store(tmp_path, fake)
    events = tell(memory, billed, "kept", "C-101", [*customer("C-101")["sessions"][0], MOVED])
    replaced = [event for event in events if event["event"] == "replaced"]
    assert len(replaced) == 1 and "Mill Lane" in replaced[0]["was"]
    saved = texts(memory, "C-101")
    assert not any("Mill Lane" in text for text in saved) and any("Harbour Road" in text for text in saved)
    changes = [row for row in memory.history(replaced[0]["id"]) if row["event"] == "UPDATE"]
    assert "Mill Lane" in changes[-1]["old_memory"]
    assert score(memory, customer("C-101"))["wrong"] == []


def test_forget_scrubs_the_vector_store_history_and_the_messages_mem0_rereads(tmp_path):
    fake = FakeOpenAI(decisions={FORGET: [{"do": "forget", "id": "900461"}]})
    memory, billed = open_store(tmp_path, fake)
    events = tell(memory, billed, "kept", "C-103", [*customer("C-103")["sessions"][0], FORGET])
    assert [event["event"] for event in events].count("forgotten") == 1
    assert not any("900461" in text for text in texts(memory, "C-103"))
    rows = memory.db.connection.execute("SELECT old_memory, new_memory FROM history").fetchall()
    assert not any("900461" in str(row) for row in rows)
    # Mem0 feeds its extractor the customer's recent messages; the next one must not see the number.
    tell(memory, billed, "kept", "C-103", ["The greyhounds like the fish recipe best."])
    assert "900461" not in fake.extraction_prompts[-1]


def test_a_conflicting_fact_waits_for_the_customer_and_is_not_recalled(tmp_path):
    ask = {"do": "ask", "old": "allergic", "new": "jerky", "question": "Is Biscuit still allergic to chicken?"}
    memory, billed = open_store(tmp_path, FakeOpenAI(decisions={TREATS: [ask]}))
    events = tell(memory, billed, "kept", "C-101", [*customer("C-101")["sessions"][0], TREATS])
    asked = [event for event in events if event["event"] == "asked"]
    assert len(asked) == 1 and asked[0]["question"] == ask["question"]
    assert not any("jerky" in item["memory"] for item in recall(memory, "C-101", "chicken jerky treats"))
    assert score(memory, customer("C-101"))["asks_met"] == 1
    # The customer says the old fact still holds: the waiting one goes, the allergy stays.
    settle(memory, "C-101", asked[0]["id"], use_new=False)
    saved = texts(memory, "C-101")
    assert not any("jerky" in text for text in saved) and any("allergic" in text for text in saved)


def test_settling_for_the_new_fact_replaces_the_old_one(tmp_path):
    ask = {"do": "ask", "old": "allergic", "new": "jerky", "question": "Is Biscuit still allergic?"}
    memory, billed = open_store(tmp_path, FakeOpenAI(decisions={TREATS: [ask]}))
    events = tell(memory, billed, "kept", "C-101", [*customer("C-101")["sessions"][0], TREATS])
    waiting = next(event for event in events if event["event"] == "asked")
    result = settle(memory, "C-101", waiting["id"], use_new=True)
    assert result["event"] == "replaced" and "allergic" in result["was"]
    saved = texts(memory, "C-101")
    assert sum("jerky" in text for text in saved) == 1 and not any("allergic" in text for text in saved)


def test_an_id_the_model_invented_changes_nothing(tmp_path):
    fake = FakeOpenAI(decisions={MOVED: [{"do": "forget", "id": "99"}, {"do": "replace", "old": "42", "new": "7"}]})
    memory, billed = open_store(tmp_path, fake)
    events = tell(memory, billed, "kept", "C-101", [*customer("C-101")["sessions"][0], MOVED])
    assert {event["event"] for event in events} == {"saved"}
    assert len(texts(memory, "C-101")) == 3


def test_the_customer_edits_a_memory_in_the_panel(tmp_path):
    memory, billed = open_store(tmp_path, FakeOpenAI())
    tell(memory, billed, "kept", "C-106", customer("C-106")["sessions"][0])
    target = all_memories(memory, "C-106")[0]
    edit(memory, target["id"], "Rolo can't have beef or pork.")
    assert texts(memory, "C-106") == ["Rolo can't have beef or pork."]
    forget(memory, "C-106", target["id"])
    assert texts(memory, "C-106") == []


def test_a_new_session_answers_from_memories_and_bills_every_call(tmp_path):
    memory, billed = open_store(tmp_path, FakeOpenAI(reply="Your boxes go to Harbour Road."))
    tell(memory, billed, "kept", "C-101", ["Boxes go to 3 Harbour Road, Falmouth."])
    turn = chat(memory, billed, "kept", "C-101", "Where do my boxes go?")
    assert turn["reply"] == "Your boxes go to Harbour Road." and "Harbour Road" in turn["recalled"][0]["text"]
    # One extraction and one reconcile per saved message, plus the reply.
    assert billed.bill["calls"] == 5 and billed.bill["cost"] == pytest.approx(0.0005)


def test_words_match_at_a_word_edge_and_a_correction_is_not_wrong():
    truth = customer("C-104")
    assert says("Bruno weighs 34kg", ["34"]) and not says("Bruno is 134cm long", ["34"])
    assert verdict("Bruno weighs 38kg", truth) == "wrong"
    assert verdict("Bruno went from 38kg to 34kg", truth) == "confirmed"
    assert verdict("Marcus likes rain", truth) == "unconfirmed"


def test_every_expected_word_is_something_the_customer_said():
    # The truth may only hold what the customer typed; a word only the model would write proves nothing.
    for truth in customers():
        said = " ".join(message for session in truth["sessions"] for message in session)
        for row in truth["facts"] + truth["gone"]:
            for word in row["words"]:
                assert any(says(said, [choice]) for choice in word.split("|")), (truth["id"], word)
        assert all(says(said, [word]) for word in truth["asks"]), truth["id"]
        assert truth["probes"] and all(re.search(r"\?$", probe) for probe in truth["probes"])


def test_serve_answers_only_its_own_page_and_forgets_from_the_panel(tmp_path):
    stores = {}

    def open_for(policy):
        stores[policy] = open_store(tmp_path / policy, FakeOpenAI())[0]
        return stores[policy]

    desk = Desk(open_for, lambda: BilledModel(FakeOpenAI(), MODEL))
    tell(stores["kept"], BilledModel(FakeOpenAI(), MODEL), "kept", "C-106", customer("C-106")["sessions"][0])
    server = serve(desk, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        state = json.loads(urllib.request.urlopen(f"{base}/api/state").read())
        assert len(state["customers"]) == 6 and set(state["policies"]) == {"append-only", "kept"}
        listed = json.loads(urllib.request.urlopen(f"{base}/api/memories?policy=kept&customer=C-106").read())
        assert len(listed) == 1 and listed[0]["verdict"] == "confirmed"
        body = json.dumps({"policy": "kept", "customer": "C-106", "id": listed[0]["id"]}).encode()
        posted = urllib.request.Request(f"{base}/api/forget", data=body, headers={"Content-Type": "application/json"})
        assert json.loads(urllib.request.urlopen(posted).read()) == {"ok": True}
        assert texts(stores["kept"], "C-106") == []
        foreign = urllib.request.Request(f"{base}/api/forget", data=body, headers={"Origin": "http://evil.example"})
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(foreign)
        assert refused.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
