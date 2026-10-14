"""The support desk: a reply is drafted, checked before sending, and judged after.

`review` runs the checks on the replies support agents already labelled, which is how the team
learns whether to trust them. Both numbers the team watches come from here: unsupported replies the
guard let through, and how often the judge agrees with the agents.
"""

from concurrent.futures import ThreadPoolExecutor

from courier.agree import Labelled, agreement
from courier.bot import draft
from courier.guard import guard
from courier.judge import judge, judge_naive
from courier.kb import HelpCentre

# What agents failed because a source does not back it. An off-question reply is grounded: the guard
# is not built to stop it, so it is the judge's job after sending.
UNSUPPORTED = ("unsupported", "unsafe")


def handle(question: str, help_centre: HelpCentre, client, model: str) -> dict:
    """A new courier message: draft from the help centre, then the grounding check decides send or hold."""
    drafted = draft(question, help_centre, client, model)
    checked = guard(question, drafted.reply, help_centre.get(drafted.cited), client, model)
    return {"question": question, "reply": drafted.reply, "cited": drafted.cited, "send": checked.send,
            "claims": checked.claims, "why": checked.why, "cost": drafted.cost + checked.cost}


def review_one(row: Labelled, help_centre: HelpCentre, client, model: str, judge_model: str) -> dict:
    sources = help_centre.get(row.cited)
    checked = guard(row.question, row.answer, sources, client, model)
    verdict = judge(row.question, row.answer, sources, client, judge_model)
    naive = judge_naive(row.question, row.answer, client, judge_model)
    return {"id": row.id, "send": checked.send, "claims": checked.claims, "why": checked.why,
            "judge": verdict.verdict, "judge_reason": verdict.reason, "checks": verdict.checks,
            "naive": naive.verdict, "naive_reason": naive.reason, "cost": checked.cost + verdict.cost + naive.cost}


def review(rows: list[Labelled], help_centre: HelpCentre, client, model: str, judge_model: str) -> dict:
    # Each reply is checked on its own, so they run side by side; the order of results is kept.
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda r: review_one(r, help_centre, client, model, judge_model), rows))
    bad = [r for r, got in zip(rows, results) if r.kind in UNSUPPORTED]
    sent_bad = [r.id for r, got in zip(rows, results) if r.kind in UNSUPPORTED and got["send"]]
    held_good = [r.id for r, got in zip(rows, results) if r.human == "pass" and not got["send"]]
    return {
        "rows": [vars(r) | got for r, got in zip(rows, results)],
        "guard": {"unsupported": len(bad), "sent": sent_bad, "held_good": held_good,
                  "held": sum(1 for got in results if not got["send"]), "total": len(rows)},
        "judge": agreement(rows, [got["judge"] for got in results]),
        "naive": agreement(rows, [got["naive"] for got in results]),
        "cost": round(sum(got["cost"] for got in results), 4),
    }
