"""Find the Safe Harbor identifiers in a visit transcript, two ways.

`regex_spans` is the naive version most teams ship first: patterns for the identifiers that have a
fixed shape. `PhiDetector` is Presidio's analyzer with a spaCy model for names, places and dates, plus
recognizers for what a spoken clinic visit adds: numbers read aloud, letters spelled out, cued names,
the clinic's own MRN format. Both return the same `Span`, so the grader scores them side by side.
"""

import json
import re
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path

from presidio_analyzer import AnalyzerEngine, EntityRecognizer, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider

CLINIC = Path("data/clinic.json")
SPACY_MODEL = "en_core_web_md"
# Below this score a span is still redacted, because a miss leaks PHI and an extra only costs a word.
# But the clinician is asked to confirm it, since an extra can also hide a dose or a lot number.
REVIEW_BELOW = 0.6
# Presidio's own entity names, mapped to the Safe Harbor types the annotators used.
SAFE_HARBOR_TYPE = {"PERSON": "NAME", "DATE_TIME": "DATE", "PHONE_NUMBER": "PHONE", "EMAIL_ADDRESS": "EMAIL",
                    "US_SSN": "SSN", "LOCATION": "ADDRESS"}
CUSTOM_TYPES = ["NAME", "DATE", "PHONE", "SSN", "MRN", "ID", "ADDRESS"]
MONTHS = ("January|February|March|April|May|June|July|August|September|October|November|December"
          "|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec")
ORDINALS = ("first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth"
            "|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|thirtieth"
            "|twenty-\\w+|thirty-first|\\d{1,2}(?:st|nd|rd|th)?")
# Safe Harbor lets a state stay in the text; only places smaller than a state are identifiers.
STATES = {"Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
          "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
          "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri",
          "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
          "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
          "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
          "West Virginia", "Wisconsin", "Wyoming", "US", "U.S.", "America", "the United States"}


@dataclass(frozen=True, order=True)
class Span:
    start: int
    end: int
    type: str
    score: float
    source: str

    @property
    def unsure(self) -> bool:
        return self.score < REVIEW_BELOW

    def as_dict(self) -> dict:
        return {**asdict(self), "unsure": self.unsure}


# The naive baseline: only the identifiers with a fixed written shape.
NAIVE_PATTERNS = {
    "PHONE": re.compile(r"\(?\b\d{3}\)?[-.\s]?\d{3}[-.\s]\d{4}\b"),
    "EMAIL": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
}


def regex_spans(text: str) -> list[Span]:
    """What a regex pass finds. It cannot see a name, a spoken date or a number read aloud."""
    return sorted(Span(match.start(), match.end(), kind, 1.0, "regex")
                  for kind, pattern in NAIVE_PATTERNS.items() for match in pattern.finditer(text))


DIGIT_WORD = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
# Five digits or more: "one or two tablets" is a dose, "four four one nine two" is a number read aloud.
SPOKEN_DIGITS = re.compile(rf"\b{DIGIT_WORD}(?:[\s,.-]+{DIGIT_WORD}){{4,}}\b", re.I)
# The cue that says what a spoken number is. Checked in priority order, because one question often
# names two things ("social security number for the insurance file").
NUMBER_CUES = [
    ("SSN", re.compile(r"\bsocial\b|\bssn\b", re.I)),
    ("MRN", re.compile(r"\bmrn\b|medical record|chart number|wristband", re.I)),
    ("ID", re.compile(r"\bmember\b|\bpolicy\b|\binsurance\b", re.I)),
    ("PHONE", re.compile(r"\bphone\b|\bcell\b|\bcall\b|\btext\b|\breach\b|\b(?:my|his|her|your|what|best) number\b", re.I)),
]


class SpokenNumberRecognizer(EntityRecognizer):
    """A number read aloud, typed by the cue in this utterance or the question before it.

    Speech-to-text writes "five five five, one two one two", which no phone or SSN pattern matches.
    With no cue the number is still redacted, at a low score, so the clinician is asked about it.
    """

    def __init__(self):
        super().__init__(supported_entities=["PHONE", "SSN", "MRN", "ID"], name="SpokenNumberRecognizer")

    def load(self) -> None:
        pass

    def analyze(self, text: str, entities: list[str], nlp_artifacts=None) -> list[RecognizerResult]:
        found = []
        for match in SPOKEN_DIGITS.finditer(text):
            # This line and the one before: a clinician's question and the patient's answer.
            line_start = text.rfind("\n", 0, text.rfind("\n", 0, match.start()))
            window = text[max(line_start, 0):match.start()]
            kind = next((cue_type for cue_type, cue in NUMBER_CUES if cue.search(window)), None)
            found.append(RecognizerResult(kind or "ID", match.start(), match.end(), 0.85 if kind else 0.5))
        return found


# A name is often announced by the words before it: a title, a relation, an introduction.
NAME = r"(?P<name>(?!(?:Dr|Doctor|Nurse)\b)[A-Z][a-z]+(?:[-'][A-Z][a-z]+)*(?:\s+[A-Z][a-z]+(?:[-'][A-Z][a-z]+)*)?)"
NAME_CUES = [
    re.compile(rf"\b(?P<name>(?:Mr|Mrs|Ms|Miss|Mx)\.?\s+[A-Z][a-z]+(?:[-'][A-Z][a-z]+)*)"),
    # The cue words are case-blind with (?i:), the name after them is not: "Jonah was" is not a full name.
    re.compile(r"(?i:\b(?:my|her|his|your|our)\s+(?:older\s+|younger\s+)?(?:wife|husband|partner|son|daughter"
               rf"|mother|father|mom|dad|brother|sister|granddaughter|grandson|niece|nephew)),?\s+{NAME}"),
    re.compile(rf"(?i:\b(?:my name is|name for the form is|I'm|I am|this must be|you're|call me|ask for|"
               rf"had me as|spelled))\s+{NAME}"),
    re.compile(rf"\b(?:Hi|Hello|Thank you|Thanks|Morning),?\s+{NAME}"),
]


class CuedNameRecognizer(EntityRecognizer):
    """Names the spaCy model can miss in speech: "my son, Erik", "Thank you, Rosa", "Mrs. Haddad"."""

    def __init__(self):
        super().__init__(supported_entities=["NAME"], name="CuedNameRecognizer")

    def load(self) -> None:
        pass

    def analyze(self, text: str, entities: list[str], nlp_artifacts=None) -> list[RecognizerResult]:
        # A cue is strong evidence, so it outranks spaCy's guess that "Olivia" is a place.
        return [RecognizerResult("NAME", match.start("name"), match.end("name"), 0.9)
                for cue in NAME_CUES for match in cue.finditer(text)]


def pattern(kind: str, name: str, regex: str, score: float, context: list[str] | None = None) -> PatternRecognizer:
    # Presidio matches case-blind by default. Capitals carry the meaning here ("Larch Street" is an
    # address, "by the way" is not), so each pattern turns that off with a scoped (?-i:) flag.
    return PatternRecognizer(supported_entity=kind, name=name, patterns=[Pattern(name, f"(?-i:{regex})", score)],
                             context=context)


def clinic_recognizers() -> list[EntityRecognizer]:
    """The recognizers this clinic adds to Presidio's defaults."""
    return [
        SpokenNumberRecognizer(),
        CuedNameRecognizer(),
        # Northgate's MRN is HL and seven digits. Every EHR has its own format; this is the line to change.
        pattern("MRN", "NorthgateMrn", r"\bHL-?\d{7}\b", 0.9),
        pattern("ID", "InsuranceMemberId", r"\b[A-Z]{3}\s?\d{3}\s?\d{3}\s?\d{3}\b", 0.75, ["member", "insurance"]),
        pattern("SSN", "DashedSsn", r"\b\d{3}-\d{2}-\d{4}\b", 0.75),
        pattern("PHONE", "UsPhone", r"\(?\b\d{3}\)?[-.\s]?\d{3}[-.\s]\d{4}\b", 0.75),
        # Spelling a name out letter by letter still spells the name.
        pattern("NAME", "SpelledOut", r"\b(?:[A-Z] ){3,}[A-Z]\b", 0.7),
        pattern("DATE", "SpokenDate", rf"\b(?:(?:{MONTHS})\.?\s+(?:[Tt]he\s+)?(?:{ORDINALS})\b"
                                      rf"|(?:[Tt]he\s+)?(?:{ORDINALS})\s+of\s+(?:{MONTHS})\b)", 0.85),
        pattern("ADDRESS", "StreetAddress", r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s+){1,3}(?:Street|St|Avenue|Ave|Road|Rd"
                                            r"|Lane|Ln|Drive|Court|Ct|Boulevard|Blvd|Way|Place|Pike)\b", 0.85),
        pattern("ADDRESS", "StreetName", r"\b(?:[A-Z][a-z]+\s+){1,2}(?:Street|Avenue|Road|Lane|Drive|Court"
                                         r"|Boulevard|Way|Place|Pike)\b", 0.65),
        # Five digits alone could be anything; next to a state or the word zip it is a ZIP code.
        pattern("ADDRESS", "ZipCode", r"\b\d{5}(?:-\d{4})?\b", 0.3, ["zip", "ohio", "oh", "code"]),
    ]


@cache
def clinic() -> dict:
    return json.loads(CLINIC.read_text())


def allowed_words() -> list[str]:
    """Clinicians and the formulary pass through untouched.

    Safe Harbor covers the patient and their relatives, employers and household. A clinician is none of
    those, and "Allegra" is a drug. Dropping either from the note would break it for no privacy gain.
    """
    words = list(clinic()["formulary"])
    for clinician in clinic()["clinicians"]:
        first, last = clinician["name"].split(" ", 1)
        words += [clinician["name"], first, last, f"Dr. {last}", f"Dr {last}", f"{clinician['name']} {clinician['title']}"]
    return words


def keep(text: str, span: Span) -> bool:
    """The Safe Harbor rule, applied after detection: a year, a weekday or a state may stay."""
    found = text[span.start:span.end]
    if span.type == "DATE":
        # spaCy tags "nineteen sixty-one" and "two weeks ago" as dates; only a month or day is PHI.
        return bool(re.search(rf"\b(?:{MONTHS})\b|\d{{1,2}}/\d{{1,2}}", found, re.I))
    if span.type == "ADDRESS" and span.source != "ZipCode":
        return found.strip() not in STATES and not found.isdigit()
    return True


class PhiDetector:
    """Presidio's analyzer, set up once: loading the spaCy model takes seconds, analysing takes milliseconds."""

    def __init__(self, spacy_model: str = SPACY_MODEL):
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy", "models": [{"lang_code": "en", "model_name": spacy_model}]})
        self.analyzer = AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])
        for recognizer in clinic_recognizers():
            self.analyzer.registry.add_recognizer(recognizer)
        self.entities = sorted(set(SAFE_HARBOR_TYPE) | set(CUSTOM_TYPES))
        self.allow_list = allowed_words()

    def spans(self, text: str, language: str = "en", clinic_recognizers: bool = True) -> list[Span]:
        """Every identifier found. Raises when Presidio cannot read the text; the caller must not send it.

        `clinic_recognizers=False` asks only for Presidio's built-in entities, to show what they miss.
        """
        entities = self.entities if clinic_recognizers else sorted(SAFE_HARBOR_TYPE)
        results = self.analyzer.analyze(text=text, language=language, entities=entities,
                                        allow_list=self.allow_list, score_threshold=0.4)
        found = [Span(result.start, result.end, SAFE_HARBOR_TYPE.get(result.entity_type, result.entity_type),
                      round(result.score, 2), (result.recognition_metadata or {}).get("recognizer_name", ""))
                 for result in results]
        found = [span for span in found if keep(text, span)]
        return merge(found + same_name_elsewhere(text, found)) if clinic_recognizers else merge(found)


def same_name_elsewhere(text: str, spans: list[Span]) -> list[Span]:
    """A name found once is redacted everywhere it appears.

    "With Luz and my son" has no cue and the model does not tag it, but "my granddaughter Luz" earlier
    in the visit did. One clear mention is evidence for every other one.
    """
    names = {text[span.start:span.end] for span in spans if span.type == "NAME"}
    return [Span(match.start(), match.end(), "NAME", 0.7, "SameNameElsewhere")
            for name in names for match in re.finditer(rf"\b{re.escape(name)}\b", text)]


def merge(spans: list[Span]) -> list[Span]:
    """One span per stretch of text. Two recognizers often find the same phone number; the surest names it."""
    merged: list[Span] = []
    for span in sorted(spans):
        if merged and span.start < merged[-1].end:
            last = merged[-1]
            best = max(last, span, key=lambda each: each.score)
            merged[-1] = Span(last.start, max(last.end, span.end), best.type, best.score, best.source)
        else:
            merged.append(span)
    return merged


@cache
def detector() -> PhiDetector:
    return PhiDetector()
