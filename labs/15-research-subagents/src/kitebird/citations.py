"""The citation check: does the source each claim cites actually say it?

A model writes fluent citations whether or not the source agrees, so the check never asks the writer.
First the code looks for the claim's quote, character for character, in the cited source. Only a quote
that is really there goes to a model, which judges whether the source supports the claim.
"""

import asyncio
import difflib
import json

from kitebird.corpus import Source
from kitebird.searcher import add_usage, usage_of

SHORTEST_QUOTE = 5  # words; "sales grew" appears in many sources and proves nothing
JUDGES_AT_ONCE = 8
# Curly quotes and long dashes are the usual way a copied quote drifts from its source without lying.
STRAIGHTEN = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
# Neither failure is final. A missing quote needs a person to read the source, and the judge is a model
# that can be wrong, so both go to the editor, who keeps or cuts the claim.
FOR_THE_EDITOR = ("quote missing", "not supported")

JUDGE = """Decide whether the quoted sentences, read in their source, support the claim.
Judge the substance: every number, date, place and actor in the claim must match the source. Wording may differ, and hedges such as "reported" or "about" are fine.
A claim that adds a fact, a comparison or advice that the quoted sentences do not state is not supported.
Return JSON: {"supports": true or false, "reason": "one short sentence"}"""


def normal(text: str) -> str:
    """Case, spacing and quote marks change; nothing else does. Lower-casing keeps every index in place."""
    return " ".join(text.translate(STRAIGHTEN).lower().split())


def locate(quote: str, source: Source) -> tuple[int, int] | None:
    """Where the quote sits in the source text, so the page can highlight it; None if it is not there."""
    wanted = normal(quote).strip(" \"'.").removesuffix("...").strip()
    if not wanted:
        return None
    start = normal(source.text).find(wanted)
    return (start, start + len(wanted)) if start >= 0 else None


def closest(quote: str, source: Source) -> str:
    """The source sentence most like the quote, shown to the editor beside the claim."""
    return max(source.sentences(), key=lambda sentence: difflib.SequenceMatcher(None, normal(quote), normal(sentence)).ratio())


def find_quote(claim: dict, sources: dict[str, Source]) -> dict:
    """The check in code. Every quote must sit in the cited source; returns their spans, or why the
    claim goes to the editor. One missing quote is enough, because the claim leans on all of them."""
    cited = sources.get((claim.get("source") or "").strip().upper())
    quotes = claim.get("quotes") or []
    if not cited or not quotes:
        return {"verdict": "quote missing", "reason": "The claim cites no source the library holds."}
    spans = []
    for quote in quotes:
        if len(quote.split()) < SHORTEST_QUOTE:
            return {"verdict": "quote missing", "reason": f"A quote has fewer than {SHORTEST_QUOTE} words, too short to show the source says it.",
                    "missing": quote, "closest": closest(quote, cited)}
        span = locate(quote, cited)
        if not span:
            elsewhere = [source_id for source_id, source in sources.items() if locate(quote, source)]
            reason = (f"The quote is in {elsewhere[0]}, not in {cited.id}, the source the claim cites." if elsewhere
                      else f"The quote does not appear in {cited.id}. The nearest sentence there is shown.")
            return {"verdict": "quote missing", "reason": reason, "missing": quote, "closest": closest(quote, cited)}
        spans.append(list(span))
    return {"verdict": "quoted", "spans": sorted(spans)}


async def judge(claim: dict, source: Source, client, model: str, usage: dict, gate: asyncio.Semaphore) -> dict:
    """The model check, only for quotes that are really in the source. The judge sees the whole source,
    so a claim can lean on the date in the title, but it rules on what the quoted sentences state."""
    async with gate:
        response = await client.chat.completions.create(
            model=model, response_format={"type": "json_object"}, extra_body={"usage": {"include": True}},
            messages=[{"role": "system", "content": JUDGE}, {"role": "user", "content":
                      f"Source {source.id}: {source.title} ({source.publisher}, {source.date})\n{source.text}\n\n"
                      "Quoted sentences: " + " ".join(f'"{quote}"' for quote in claim["quotes"]) + f"\nClaim: {claim['text']}"}])
    add_usage(usage, usage_of(response, model))
    try:
        verdict = json.loads(response.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        verdict = {}
    supports = verdict.get("supports") is True
    return {"verdict": "supported" if supports else "not supported", "reason": str(verdict.get("reason", ""))}


async def check_report(report: dict, brief: dict, sources: dict[str, Source], client, model: str, usage: dict) -> dict:
    """Mark every claim supported, not supported or quote missing, and score the report."""
    claims = [claim for section in report["sections"] for claim in section["claims"]]
    for number, claim in enumerate(claims, 1):
        claim["id"] = f"C{number}"
        claim.update(find_quote(claim, sources))
    gate = asyncio.Semaphore(JUDGES_AT_ONCE)
    quoted = [claim for claim in claims if claim["verdict"] == "quoted"]
    verdicts = await asyncio.gather(*(judge(claim, sources[claim["source"].strip().upper()], client, model, usage, gate) for claim in quoted))
    for claim, verdict in zip(quoted, verdicts):
        claim.update(verdict)
    return score(claims, brief)


def score(claims: list[dict], brief: dict) -> dict:
    """The metric: the share of claims whose cited source supports them. A question counts as covered
    only when a supported claim cites one of the sources that answer it, so a wrong claim covers nothing."""
    supported = [claim for claim in claims if claim["verdict"] == "supported"]
    cited = {claim["source"].strip().upper() for claim in supported}
    covered = [aspect["id"] for aspect in brief["aspects"] if cited & set(aspect["sources"])]
    return {"claims": len(claims), "supported": len(supported),
            "not_supported": sum(claim["verdict"] == "not supported" for claim in claims),
            "quote_missing": sum(claim["verdict"] == "quote missing" for claim in claims),
            "editor": sum(claim["verdict"] in FOR_THE_EDITOR for claim in claims),
            "share": round(len(supported) / len(claims), 3) if claims else 0.0,
            "covered": covered, "aspects": len(brief["aspects"]),
            "sources": len({(claim.get("source") or "").strip().upper() for claim in claims if claim.get("source")})}
