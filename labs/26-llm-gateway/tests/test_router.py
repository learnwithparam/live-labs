"""The router's rules, with a scripted classifier: cheap only when the rules and the label agree."""

import asyncio

from fakes import broken_classifier, classifier
from fernway.router import read_label, route


def decide(text: str, classify):
    return asyncio.run(route(text, classify))


def test_a_plain_lookup_goes_to_the_cheap_tier():
    decision = decide("Is this oat milk sweetened?", classifier("lookup"))
    assert decision.tier == "cheap" and not decision.flagged


def test_an_allergy_question_goes_strong_without_asking_the_classifier():
    asked: list[str] = []
    decision = decide("Is this safe for my son's peanut allergy?", classifier("lookup", asked))
    assert decision.tier == "strong" and asked == [] and decision.signals == ("safety",)


def test_judgment_goes_strong_unflagged():
    decision = decide("Refund me for the bruised pears.", classifier("judgment"))
    assert decision.tier == "strong" and not decision.flagged


def test_a_cheap_label_on_a_request_that_compares_amounts_goes_strong_and_is_flagged():
    decision = decide("It came over an hour late.", classifier("lookup"))
    assert decision.tier == "strong" and decision.flagged and "'over'" in decision.reason


def test_a_classifier_outage_fails_closed_to_the_strong_tier():
    decision = decide("Is this oat milk sweetened?", broken_classifier())
    assert decision.tier == "strong" and decision.flagged and "ConnectionError" in decision.reason


def test_a_reply_with_no_known_label_counts_as_unclear():
    assert read_label("Sure! I think it is simple.") == "unclear"
    assert read_label("Extract.") == "extract"
    decision = decide("Hello", classifier("maybe"))
    assert decision.tier == "strong" and decision.flagged


def test_packs_inside_packs_go_strong_without_asking_the_classifier():
    asked: list[str] = []
    decision = decide("CASE 4 trays x 6 cans Cola 330ml", classifier("extract", asked))
    assert decision.tier == "strong" and asked == [] and decision.signals == ("packs",)
