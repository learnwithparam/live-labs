"""The scribe pipeline: detect, redact, write the note on the redacted text, re-identify for the clinician.

Two rules hold the privacy promise. The model is only ever called with the redacted transcript. And a
transcript the analyzer cannot read is held for the clinician, never sent raw: failing open would
turn every analyzer bug into a disclosure.
"""

import re
from typing import Callable

from wrenfield.config import PRICES
from wrenfield.detect import Span, merge
from wrenfield.redact import Vault, redact, reidentify

NOTE_INSTRUCTIONS = """Write a clinical visit note from the transcript, in four short sections: Subjective, \
Objective, Assessment, Plan. Use only facts stated in the transcript.

Identifiers were replaced with placeholders in square brackets, such as [Person A], [DATE_1] or [PHONE_1]. \
When the note needs one, copy the placeholder exactly. Never guess the value behind a placeholder and never \
make up a new one. Clinician names and drug names are real and may be used as written. Plain text, no markdown. \
Keep it under 220 words."""


def apply_review(spans: list[Span], review: dict | None) -> list[Span]:
    """The clinician's decisions win over the detector's.

    A released span goes to the model as written (a lot number, not PHI). A confirmed or added span is
    redacted at full confidence, so it no longer waits for review.
    """
    if not review:
        return spans
    released = {(item["start"], item["end"]) for item in review.get("release", [])}
    confirmed = {(item["start"], item["end"]) for item in review.get("confirm", [])}
    kept = [Span(span.start, span.end, span.type, 1.0, "Clinician") if (span.start, span.end) in confirmed else span
            for span in spans if (span.start, span.end) not in released]
    added = [Span(item["start"], item["end"], item["type"], 1.0, "Clinician") for item in review.get("add", [])]
    return merge(kept + added)


def prepare(one_visit: dict, find: Callable[[str, str], list[Span]], review: dict | None = None) -> dict:
    """Redact one visit, or hold it. Nothing in a held visit is ever passed to the model."""
    try:
        spans = apply_review(find(one_visit["transcript"], one_visit["language"]), review)
        redacted, vault = redact(one_visit["transcript"], spans)
    except Exception as error:
        return {"id": one_visit["id"], "status": "held", "spans": [], "redacted": None, "vault": Vault(),
                "reason": f"The analyzer could not read this transcript ({type(error).__name__}: {error}). "
                          "It was not sent to the model."}
    unsure = [span for span in spans if span.unsure]
    return {"id": one_visit["id"], "status": "sent", "spans": spans, "redacted": redacted, "vault": vault,
            "unsure": unsure, "reason": None}


def usage_of(response, model: str) -> dict:
    """What the provider reported for one call. The bill is the truth; list prices are the fallback."""
    usage = response.usage
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    # OpenRouter adds the billed cost when asked with usage.include; other providers do not.
    billed = (getattr(usage, "model_extra", None) or {}).get("cost")
    price_in, price_cached, price_out = PRICES.get(model, (0.0, 0.0, 0.0))
    listed = ((usage.prompt_tokens - cached) * price_in + cached * price_cached + usage.completion_tokens * price_out) / 1e6
    return {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "cost": float(billed) if billed is not None else listed, "billed": billed is not None}


def write_note(client, model: str, redacted: str) -> tuple[str, list[dict], dict]:
    """One model call on the redacted transcript. Returns the raw note, the exact messages sent, and usage."""
    messages = [{"role": "system", "content": NOTE_INSTRUCTIONS},
                {"role": "user", "content": f"Transcript:\n\n{redacted}"}]
    response = client.chat.completions.create(model=model, messages=messages,
                                              extra_body={"usage": {"include": True}})
    return response.choices[0].message.content or "", messages, usage_of(response, model)


def document(one_visit: dict, find: Callable[[str, str], list[Span]], client, model: str,
             review: dict | None = None) -> tuple[dict, Vault]:
    """The full visit: a record that is safe to store and log, and the vault that stays in the clinic."""
    prepared = prepare(one_visit, find, review)
    record = {"id": one_visit["id"], "status": prepared["status"], "reason": prepared["reason"],
              "redacted": prepared["redacted"], "spans": [span.as_dict() for span in prepared["spans"]],
              "messages": [], "note": None, "unknown_placeholders": [], "usage": None}
    if prepared["status"] == "held":
        return record, prepared["vault"]
    note, messages, usage = write_note(client, model, prepared["redacted"])
    _, unknown = reidentify(note, prepared["vault"])
    # The stored note keeps its placeholders. Re-identification happens when a clinician opens it.
    record.update(messages=messages, note=note, unknown_placeholders=unknown, usage=usage)
    return record, prepared["vault"]


def leaked_phi(one_visit: dict, texts: list[str]) -> list[str]:
    """Every annotated identifier that appears, as a whole word, in any of `texts`."""
    joined = "\n".join(" ".join(text.split()).casefold() for text in texts if text)
    return sorted({span["text"] for span in one_visit["phi"]
                   if re.search(rf"(?<!\w){re.escape(' '.join(span['text'].split()).casefold())}(?!\w)", joined)})
