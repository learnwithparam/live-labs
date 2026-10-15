"""Swap each identifier for a stable placeholder before the model, and swap it back after.

The model needs to know that the person in line 2 is the person in line 9, or the note falls apart.
So the same name always becomes the same placeholder, "[Person A]", and dates become "[DATE_1]".
The vault that maps placeholders back stays inside the clinic; only the placeholders cross the wire.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from presidio_anonymizer import AnonymizerEngine, DeanonymizeEngine
from presidio_anonymizer.entities import OperatorConfig, OperatorResult, RecognizerResult
from presidio_anonymizer.operators import Operator, OperatorType

from wrenfield.detect import Span

# What the note writer may emit: "[Person A]" for names, "[DATE_1]" style for everything else.
PLACEHOLDER = re.compile(r"\[(?:Person [A-Z]{1,2}|[A-Z]+_\d+)\]")


def letters(number: int) -> str:
    """1 is A, 26 is Z, 27 is AA: a person label that reads like a name in the note."""
    label = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(65 + remainder) + label
    return label


HONORIFICS = {"mr", "mrs", "ms", "miss", "mx"}


def name_words(name: str) -> set[str]:
    return {word.strip(".,").casefold() for word in name.split()} - HONORIFICS - {""}


@dataclass
class Vault:
    """Placeholder to original, built in reading order so the first date in the visit is DATE_1."""

    entries: dict[str, dict] = field(default_factory=dict)
    by_original: dict[tuple[str, str], str] = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)

    def token_for(self, kind: str, original: str) -> str:
        # "Luz" and "luz" are one person; case and spacing are speech-to-text noise, not a new identity.
        key = (kind, " ".join(original.split()).casefold())
        if key not in self.by_original and kind == "NAME":
            # "Rosa" after "Rosa Delgado", or "Leila Haddad" after "Mrs. Haddad", is the same person to
            # the note. Two relatives who share only a surname would merge too; the clinician reads the note.
            words = name_words(original)
            same = next((token for (other_kind, other), token in self.by_original.items() if other_kind == "NAME"
                         and words and (words <= name_words(other) or name_words(other) <= words)), None)
            if same:
                self.by_original[key] = same
        if key not in self.by_original:
            self.counts[kind] += 1
            number = self.counts[kind]
            token = f"[Person {letters(number)}]" if kind == "NAME" else f"[{kind}_{number}]"
            self.by_original[key] = token
            self.entries[token] = {"original": original, "type": kind}
        return self.by_original[key]


class Pseudonym(Operator):
    """Presidio anonymizer operator: the identifier becomes its placeholder from the vault."""

    def operate(self, text: str, params: dict = None) -> str:
        return params["vault"].token_for(params["entity_type"], text)

    def validate(self, params: dict = None) -> None:
        if not isinstance((params or {}).get("vault"), Vault):
            raise ValueError("the pseudonym operator needs the visit's vault")

    def operator_name(self) -> str:
        return "pseudonym"

    def operator_type(self) -> OperatorType:
        return OperatorType.Anonymize


class Reidentify(Operator):
    """Presidio deanonymize operator: a placeholder becomes the original, for the clinician's eyes only."""

    def operate(self, text: str, params: dict = None) -> str:
        entry = params["vault"].entries.get(text)
        # A placeholder the model made up has no original. Leave it visible so the clinician sees it.
        return entry["original"] if entry else text

    def validate(self, params: dict = None) -> None:
        if not isinstance((params or {}).get("vault"), Vault):
            raise ValueError("the reidentify operator needs the visit's vault")

    def operator_name(self) -> str:
        return "reidentify"

    def operator_type(self) -> OperatorType:
        return OperatorType.Deanonymize


ANONYMIZER = AnonymizerEngine()
ANONYMIZER.add_anonymizer(Pseudonym)
DEANONYMIZER = DeanonymizeEngine()
DEANONYMIZER.add_deanonymizer(Reidentify)


def redact(text: str, spans: list[Span]) -> tuple[str, Vault]:
    """The transcript the model sees, and the vault that undoes it."""
    vault = Vault()
    for span in sorted(spans):
        vault.token_for(span.type, text[span.start:span.end])
    results = [RecognizerResult(span.type, span.start, span.end, span.score) for span in spans]
    # The spans are already merged, so Presidio must not join two neighbours into one placeholder.
    redacted = ANONYMIZER.anonymize(text=text, analyzer_results=results, merge_entities_with_spaces=False,
                                    operators={"DEFAULT": OperatorConfig("pseudonym", {"vault": vault})})
    return redacted.text, vault


def reidentify(note: str, vault: Vault) -> tuple[str, list[str]]:
    """The note with real names and dates back in, plus any placeholder the vault does not know."""
    found = [OperatorResult(match.start(), match.end(), "PLACEHOLDER", match.group(), "reidentify")
             for match in PLACEHOLDER.finditer(note)]
    unknown = sorted({match.text for match in found if match.text not in vault.entries})
    if not found:
        return note, unknown
    restored = DEANONYMIZER.deanonymize(text=note, entities=found,
                                        operators={"DEFAULT": OperatorConfig("reidentify", {"vault": vault})})
    return restored.text, unknown
