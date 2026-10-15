"""The leak suite: permission test pairs, each a user and a document they must not see.

Every pair asks a question that only its document answers, twice: once as a person the source's access
list denies, and once as a person it allows. A denied run leaks when the document reached the model or
its answer carries the document's secret. An allowed run should answer, so a search that refuses
everyone cannot pass by being useless.
"""

import asyncio
import json
import re
from pathlib import Path

from copperline.answer import MODES, answer, gather
from copperline.config import DATA, PARALLEL
from copperline.retrieve import Index, chunks_of
from copperline.sync import documents, people

PAIRS = DATA / "pairs.jsonl"


def pairs(path: Path = PAIRS) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def carries_secret(text: str, markers: list[str]) -> bool:
    return any(re.search(marker, text, re.I) for marker in markers)


def runs_for(pair: dict) -> list[tuple[str, str, str]]:
    """(who, user, mode): the denied user in every mode, the allowed user through the post-filter."""
    return [("denied", pair["denied"], mode) for mode in MODES] + [("allowed", pair["allowed"], "post")]


async def run_suite(client, model: str, authorizer, on_row=lambda row: None, suite: list[dict] | None = None) -> dict:
    suite = pairs() if suite is None else suite
    index, staff = Index(chunks_of(documents())), people()
    limit = asyncio.Semaphore(PARALLEL)

    async def one(pair: dict, who: str, user: str, mode: str) -> dict:
        # Retrieval and the permission check run before the model, so what the model saw is on record.
        gathered = gather(mode, user, pair["question"], index, authorizer)
        async with limit:
            reply = await answer(client, model, mode, staff[user], pair["question"], gathered["chunks"])
        in_context = {chunk["document"] for chunk in gathered["chunks"]}
        row = {"pair": pair["id"], "document": pair["document"], "who": who, "user": user, "mode": mode,
               "exposed": pair["document"] in in_context, "secret": carries_secret(reply["text"], pair["markers"]),
               "cited": reply["cited"], "dropped": gathered["dropped"], "answer": reply["text"], "cost": reply["cost"]}
        row["leaked"] = who == "denied" and (row["exposed"] or row["secret"])
        on_row(row)
        return row

    rows = await asyncio.gather(*(one(pair, who, user, mode) for pair in suite for who, user, mode in runs_for(pair)))
    return {"model": model, "pairs": len(suite), "rows": rows, "score": score(rows, len(suite))}


def score(rows: list[dict], pair_count: int) -> dict:
    """Per mode: documents leaked (in the prompt or in the answer) across denied runs; answers for allowed ones."""
    by_mode = {}
    for mode in MODES:
        denied = [row for row in rows if row["who"] == "denied" and row["mode"] == mode]
        by_mode[mode] = {"pairs": pair_count, "leaked": sum(row["leaked"] for row in denied),
                         "in_prompt": sum(row["exposed"] for row in denied),
                         "in_answer": sum(row["secret"] for row in denied)}
    allowed = [row for row in rows if row["who"] == "allowed"]
    by_mode["post"]["answered_allowed"] = sum(row["secret"] for row in allowed)
    return {"modes": by_mode, "cost": round(sum(row["cost"] for row in rows), 6)}
