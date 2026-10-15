"""`scribe`: redact one visit, grade both detectors, write every note on a real model, or open the editor."""

import argparse
import json
import sys
from pathlib import Path

GRADE = Path("out/grade.json")
NOTES = Path("out/notes.json")
# The vault holds real identifiers. In production it lives in the clinic's own database, never in logs.
VAULT = Path("out/vault.json")
TIMEOUT = 90  # seconds per model call
DETECTORS = {"regex": "Regex only", "presidio-defaults": "Presidio as installed", "presidio": "Presidio + clinic"}


def model():
    from openai import OpenAI

    from wrenfield.config import llm_settings

    base_url, api_key, model_name = llm_settings()
    return OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2), model_name


def finder(name: str):
    """A detector as `find(text, language)`, the shape the grader and the pipeline both take."""
    from wrenfield.detect import detector, regex_spans

    if name == "regex":
        return lambda text, language: regex_spans(text)
    return lambda text, language: detector().spans(text, language, clinic_recognizers=name == "presidio")


def show_visit(visit_id: str, detector_name: str) -> int:
    from wrenfield.grade import visit
    from wrenfield.scribe import leaked_phi, prepare

    chosen = visit(visit_id)
    prepared = prepare(chosen, finder(detector_name))
    print(f"{chosen['id']}  {chosen['reason']}  with {chosen['clinician']}. Detector: {DETECTORS[detector_name]}\n")
    if prepared["status"] == "held":
        print(f"HELD. {prepared['reason']}")
        return 0
    print(prepared["redacted"])
    leaked = leaked_phi(chosen, [prepared["redacted"]])
    print(f"\n{len(prepared['spans'])} spans redacted. Annotated PHI still in the text: "
          + (", ".join(repr(text) for text in leaked) if leaked else "none"))
    for span in prepared["unsure"]:
        print(f"Needs the clinician: {chosen['transcript'][span.start:span.end]!r} "
              f"({span.type}, score {span.score}, from {span.source})")
    return 0


def grade_all() -> int:
    from wrenfield.grade import grade, visits

    english = [each for each in visits() if each["language"] == "en"]
    held = [each["id"] for each in visits() if each["language"] != "en"]
    print(f"{len(english)} English visits, {sum(len(each['phi']) for each in english)} PHI spans a person annotated. "
          f"Held, never graded or sent: {', '.join(held) or 'none'}.\n")
    results = {name: grade(english, lambda one, name=name: finder(name)(one["transcript"], "en"))
               for name in DETECTORS}
    kinds = list(results["presidio"]["by_type"])
    print(f"{'':<24}{'recall':<9}{'missed':<8}{'extra':<7}" + "".join(f"{kind:<9}" for kind in kinds))
    for name, result in results.items():
        caught = "".join(f"{counts['total'] - counts['missed']}/{counts['total']}".ljust(9)
                         for counts in (result["by_type"][kind] for kind in kinds))
        print(f"{DETECTORS[name]:<24}{result['recall']:<9}{result['missed']:<8}{result['extra']:<7}{caught}")
    best = results["presidio"]
    print(f"\nPresidio + clinic: PHI missed {best['missed']} of {best['spans']}, "
          f"precision {best['precision']} ({best['extra']} extra of {best['detected']} redacted).")
    for row in best["rows"]:
        for extra in row["extra"]:
            print(f"  extra in {row['id']}: {extra['text']!r} ({extra['type']}, score {extra['score']})")
    GRADE.parent.mkdir(exist_ok=True)
    GRADE.write_text(json.dumps(results, indent=1))
    return 0


def write_notes() -> int:
    from wrenfield.grade import visits
    from wrenfield.scribe import document, leaked_phi

    client, model_name = model()
    records, vaults, leaks, cost = {}, {}, 0, 0.0
    print(f"Writing {len(visits())} visit notes with {model_name}, from redacted transcripts only.\n")
    for one_visit in visits():
        record, vault = document(one_visit, finder("presidio"), client, model_name)
        records[one_visit["id"]], vaults[one_visit["id"]] = record, vault.entries
        if record["status"] == "held":
            print(f"  {one_visit['id']}  HELD  {record['reason']}")
            continue
        # Everything that left the clinic for this visit: the prompt, and the note the model returned.
        sent = [message["content"] for message in record["messages"]] + [record["note"]]
        leaked = leaked_phi(one_visit, sent)
        leaks += len(leaked)
        cost += record["usage"]["cost"]
        print(f"  {one_visit['id']}  sent  {len(record['spans'])} redacted, PHI in prompt or note: {len(leaked)}"
              f"{'  ' + ', '.join(leaked) if leaked else ''}"
              f"{'  unknown placeholders: ' + ', '.join(record['unknown_placeholders']) if record['unknown_placeholders'] else ''}")
    NOTES.parent.mkdir(exist_ok=True)
    NOTES.write_text(json.dumps({"model": model_name, "records": records}, indent=1))
    VAULT.write_text(json.dumps(vaults, indent=1))
    sent_count = sum(record["status"] == "sent" for record in records.values())
    print(f"\n{sent_count} notes written, {len(records) - sent_count} held. "
          f"Annotated PHI that reached the model: {leaks}. Cost ${cost:.4f}.")
    return 1 if leaks else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="scribe")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("redact", help="show what the model would see for one visit")
    one.add_argument("visit", help="a visit id, WF-01 to WF-14")
    one.add_argument("--detector", choices=list(DETECTORS), default="presidio")
    sub.add_parser("grade", help="score every detector on the annotated visits; no key needed")
    sub.add_parser("notes", help="write every visit note on the real model")
    from wrenfield.config import PORT

    sub.add_parser("serve").add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if args.command == "redact":
        sys.exit(show_visit(args.visit, args.detector))
    if args.command == "grade":
        sys.exit(grade_all())
    if args.command == "notes":
        sys.exit(write_notes())
    from wrenfield.serve import main as serve

    sys.exit(serve(args.port))
