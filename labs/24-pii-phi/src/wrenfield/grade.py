"""Score a detector against the PHI spans a person annotated.

Recall is the number that matters: a missed span is a patient identifier sent to a model vendor.
Precision is graded too, because every extra span blanks a word the note needed, like a drug or a dose.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Callable

from wrenfield.detect import Span

VISITS = Path("data/visits.jsonl")


def visits() -> list[dict]:
    return [json.loads(line) for line in VISITS.read_text().splitlines() if line.strip()]


def visit(visit_id: str) -> dict:
    found = next((each for each in visits() if each["id"] == visit_id), None)
    if not found:
        raise SystemExit(f"No visit {visit_id}. The ids are WF-01 to WF-{len(visits()):02d}.")
    return found


def covered(text: str, annotation: dict, spans: list[Span]) -> bool:
    """Caught only when every letter and digit of the annotated span is inside some detected span.

    Half a name is still a leak: "Daniel [Person A]" tells the vendor who it is. Spaces and commas
    left outside a span carry nothing, so they do not count.
    """
    inside = set()
    for span in spans:
        inside.update(range(span.start, span.end))
    return all(position in inside for position in range(annotation["start"], annotation["end"])
               if text[position].isalnum())


def overlaps(span: Span, annotation: dict) -> bool:
    return span.start < annotation["end"] and annotation["start"] < span.end


def grade_visit(one_visit: dict, spans: list[Span]) -> dict:
    text, annotations = one_visit["transcript"], one_visit["phi"]
    missed = [annotation for annotation in annotations if not covered(text, annotation, spans)]
    # An extra span touches no annotation at all: a word the person said was safe to send.
    extra = [span for span in spans if not any(overlaps(span, annotation) for annotation in annotations)]
    return {"id": one_visit["id"], "total": len(annotations), "missed": missed,
            "extra": [{**span.as_dict(), "text": text[span.start:span.end]} for span in extra],
            "detected": len(spans), "by_type": Counter(annotation["type"] for annotation in annotations),
            "missed_by_type": Counter(annotation["type"] for annotation in missed)}


def grade(rows: list[dict], find: Callable[[dict], list[Span]]) -> dict:
    """Recall per Safe Harbor type, the missed spans themselves, and precision over every detected span."""
    graded = [grade_visit(one_visit, find(one_visit)) for one_visit in rows]
    total, missed = Counter(), Counter()
    for row in graded:
        total.update(row["by_type"])
        missed.update(row["missed_by_type"])
    detected = sum(row["detected"] for row in graded)
    extra = sum(len(row["extra"]) for row in graded)
    spans_total, spans_missed = sum(total.values()), sum(missed.values())
    return {
        "visits": len(graded), "spans": spans_total, "missed": spans_missed,
        "recall": round((spans_total - spans_missed) / spans_total, 3) if spans_total else 1.0,
        "precision": round((detected - extra) / detected, 3) if detected else 1.0,
        "detected": detected, "extra": extra,
        "by_type": {kind: {"total": count, "missed": missed[kind]} for kind, count in sorted(total.items())},
        "rows": [{**row, "by_type": dict(row["by_type"]), "missed_by_type": dict(row["missed_by_type"])}
                 for row in graded],
    }
