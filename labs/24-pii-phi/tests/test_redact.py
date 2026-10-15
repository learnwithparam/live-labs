"""Keyless tests of pseudonyms: one person is one placeholder, the vault reverses it, and a placeholder the
model invents is left visible for the clinician instead of being guessed."""

from wrenfield.detect import Span
from wrenfield.redact import letters, redact, reidentify

TEXT = "Rosa Delgado came in. Rosa said Mrs. Delgado had a cough on March third. Call 937-555-0142."


def spans_for(*words: tuple[str, str]) -> list[Span]:
    found, cursor = [], 0
    for word, kind in words:
        start = TEXT.index(word, cursor)
        found.append(Span(start, start + len(word), kind, 0.9, "test"))
        cursor = start + len(word)
    return found


def test_one_person_keeps_one_placeholder_through_the_visit():
    redacted, vault = redact(TEXT, spans_for(("Rosa Delgado", "NAME"), ("Rosa", "NAME"), ("Mrs. Delgado", "NAME"),
                                             ("March third", "DATE"), ("937-555-0142", "PHONE")))
    assert redacted == "[Person A] came in. [Person A] said [Person A] had a cough on [DATE_1]. Call [PHONE_1]."
    assert vault.entries["[Person A]"] == {"original": "Rosa Delgado", "type": "NAME"}


def test_reidentify_restores_known_placeholders_and_reports_invented_ones():
    _, vault = redact(TEXT, spans_for(("Rosa Delgado", "NAME"), ("March third", "DATE")))
    restored, unknown = reidentify("[Person A] since [DATE_1]; seen with [Person B].", vault)
    assert restored == "Rosa Delgado since March third; seen with [Person B]."
    assert unknown == ["[Person B]"]


def test_a_note_with_no_placeholders_comes_back_unchanged():
    _, vault = redact(TEXT, [])
    assert reidentify("Cough, likely allergic.", vault) == ("Cough, likely allergic.", [])


def test_person_labels_run_past_z():
    assert [letters(number) for number in (1, 26, 27, 52)] == ["A", "Z", "AA", "AZ"]
