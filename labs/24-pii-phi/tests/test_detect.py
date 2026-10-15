"""Keyless tests of the detector against the annotated visits: the clinic recognizers carry recall, the
Safe Harbor filter keeps clinicians and drugs, and a number read aloud is redacted with its cue."""

import pytest

from wrenfield.detect import Span, detector, merge, regex_spans
from wrenfield.grade import covered, grade, grade_visit, visit, visits

ENGLISH = [one for one in visits() if one["language"] == "en"]


def found(visit_id: str) -> tuple[dict, list[Span]]:
    one = visit(visit_id)
    return one, detector().spans(one["transcript"], one["language"])


def texts(one: dict, spans: list[Span]) -> set[str]:
    return {one["transcript"][span.start:span.end] for span in spans}


def test_the_set_has_fourteen_visits_one_not_in_english_and_every_span_matches_its_text():
    every = visits()
    assert [one["id"] for one in every] == [f"WF-{number:02d}" for number in range(1, 15)]
    assert [one["id"] for one in every if one["language"] != "en"] == ["WF-14"]
    for one in every:
        for span in one["phi"]:
            assert one["transcript"][span["start"]:span["end"]] == span["text"], (one["id"], span)


def test_the_clinic_detector_misses_nothing_annotated_and_beats_both_baselines():
    clinic = grade(ENGLISH, lambda one: detector().spans(one["transcript"], "en"))
    defaults = grade(ENGLISH, lambda one: detector().spans(one["transcript"], "en", clinic_recognizers=False))
    naive = grade(ENGLISH, lambda one: regex_spans(one["transcript"]))
    assert clinic["missed"] == 0, [row["missed"] for row in clinic["rows"] if row["missed"]]
    assert clinic["missed"] < defaults["missed"] < naive["missed"]
    assert clinic["precision"] >= 0.95, clinic["extra"]


def test_regex_alone_misses_names_dates_and_every_number_read_aloud():
    one = visit("WF-06")
    missed = grade_visit(one, regex_spans(one["transcript"]))["missed"]
    assert {span["type"] for span in missed} == {"DATE", "NAME", "MRN", "PHONE"}


def test_an_mrn_read_aloud_is_an_mrn_because_the_speaker_said_so():
    one, spans = found("WF-06")
    spoken = next(span for span in spans if one["transcript"][span.start:span.end].startswith("four four seven"))
    assert spoken.type == "MRN" and not spoken.unsure


def test_a_spelled_name_a_nickname_and_a_relative_are_all_names():
    for visit_id, words in [("WF-08", "S I O B H A N"), ("WF-03", "Peggy"), ("WF-02", "Adaeze")]:
        one, spans = found(visit_id)
        annotation = next(span for span in one["phi"] if span["text"] == words)
        assert covered(one["transcript"], annotation, spans), (visit_id, words)


def test_clinicians_and_drugs_reach_the_model_as_said():
    # Safe Harbor covers the patient, relatives and household. The doctor and the Allegra are not PHI.
    for visit_id, safe in [("WF-01", "Alvarez"), ("WF-01", "Allegra")]:
        one, spans = found(visit_id)
        assert not any(safe in text for text in texts(one, spans)), (visit_id, safe)


def test_a_lot_number_read_aloud_with_no_cue_waits_for_the_clinician():
    one, spans = found("WF-07")
    graded = grade_visit(one, spans)
    assert [extra["text"] for extra in graded["extra"]] == ["four four one nine two"]
    assert [one["transcript"][span.start:span.end] for span in spans if span.unsure] == ["four four one nine two"]


@pytest.mark.parametrize("pieces, merged", [
    ([Span(0, 5, "NAME", 0.9, "a"), Span(3, 9, "NAME", 0.7, "b")], [(0, 9)]),
    ([Span(0, 5, "NAME", 0.9, "a"), Span(6, 9, "DATE", 0.7, "b")], [(0, 5), (6, 9)]),
])
def test_overlapping_spans_merge_into_one_placeholder(pieces, merged):
    assert [(span.start, span.end) for span in merge(pieces)] == merged
