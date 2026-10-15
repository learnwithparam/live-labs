"""Keyless tests of the pipeline: no annotated identifier reaches the model, the clinician's review wins,
and an analyzer failure holds the visit instead of sending it raw."""

from fakes import FakeOpenAI
from wrenfield.cli import finder
from wrenfield.grade import visit, visits
from wrenfield.scribe import document, leaked_phi, prepare

MODEL = "openai/gpt-6-luna"


def sent_text(record: dict) -> list[str]:
    return [message["content"] for message in record["messages"]] + [record["note"]]


def test_no_annotated_identifier_reaches_the_model_for_any_english_visit():
    client = FakeOpenAI()
    for one in visits():
        record, vault = document(one, finder("presidio"), client, MODEL)
        if one["language"] != "en":
            continue
        assert record["status"] == "sent"
        assert leaked_phi(one, sent_text(record)) == [], one["id"]
        assert record["unknown_placeholders"] == []
    # Thirteen calls: the Spanish visit never reached the model.
    assert len(client.seen) == 13


def test_regex_alone_would_have_leaked_names_and_spoken_numbers():
    one = visit("WF-06")
    record, _ = document(one, finder("regex"), FakeOpenAI(), MODEL)
    assert "Marcus" in leaked_phi(one, sent_text(record))


def test_an_analyzer_error_holds_the_visit_and_calls_nothing():
    def broken(text, language):
        raise RuntimeError("spaCy model failed to load")

    client = FakeOpenAI()
    record, _ = document(visit("WF-01"), broken, client, MODEL)
    assert record["status"] == "held" and "RuntimeError" in record["reason"]
    assert client.seen == [] and record["messages"] == [] and record["redacted"] is None


def test_a_language_the_analyzer_does_not_speak_is_held():
    client = FakeOpenAI()
    record, _ = document(visit("WF-14"), finder("presidio"), client, MODEL)
    assert record["status"] == "held" and client.seen == []


def test_releasing_a_lot_number_sends_it_and_confirming_keeps_it_redacted():
    one = visit("WF-07")
    lot = one["transcript"].index("four four one nine two")
    item = {"start": lot, "end": lot + len("four four one nine two"), "type": "ID"}
    released = prepare(one, finder("presidio"), {"release": [item]})
    assert "four four one nine two" in released["redacted"] and released["unsure"] == []
    confirmed = prepare(one, finder("presidio"), {"confirm": [item]})
    assert "four four one nine two" not in confirmed["redacted"] and confirmed["unsure"] == []


def test_a_span_the_clinician_adds_is_redacted():
    one = visit("WF-01")
    start = one["transcript"].index("stairs at work")
    prepared = prepare(one, finder("presidio"), {"add": [{"start": start, "end": start + 6, "type": "ADDRESS"}]})
    assert "stairs at work" not in prepared["redacted"] and "[ADDRESS_1] at work" in prepared["redacted"]


def test_cost_falls_back_to_list_prices_when_the_provider_reports_none():
    record, _ = document(visit("WF-01"), finder("presidio"), FakeOpenAI(cost=None), MODEL)
    assert record["usage"]["billed"] is False
    assert record["usage"]["cost"] == (900 * 0.10 + 120 * 0.50) / 1e6
