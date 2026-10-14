"""Run plain RAG and the agent on every expert question, and grade both against the expert.

Both pipelines use the same search, the same answer step and the same model, so the gap between
them is the agent's planning and retrying. Cost and model calls per question are the price of it.
"""

from concurrent.futures import ThreadPoolExecutor

from policy.grade import Question, found, grade
from policy.graph import agentic
from policy.plain import plain
from policy.search import Policies

WAYS = ("plain", "agentic")


def one(q: Question, how: str, policies: Policies, client, model: str, judge: str) -> dict:
    run = plain if how == "plain" else agentic
    got = run(q.question, policies, client, model)
    graded, grade_cost = grade(q, got["answer"], client, judge)
    return {"id": q.id, "how": how, "answer": got["answer"], "cited": got["cited"], "covered": got["covered"],
            "searches": got["searches"], "found": found(q, got["sources"]), "needs": len(q.needs),
            "cost": got["cost"], "calls": got["calls"], "grade_cost": grade_cost, **graded}


def summary(rows: list[dict], total: int) -> dict:
    return {
        "accepted": sum(r["accepted"] for r in rows),
        "total": total,
        "found": sum(r["found"] for r in rows),
        "needs": sum(r["needs"] for r in rows),
        "cost_per_question": round(sum(r["cost"] for r in rows) / total, 6),
        "calls_per_question": round(sum(r["calls"] for r in rows) / total, 1),
        # Not covered means the model said so: a person answers, nothing wrong is sent.
        "to_a_person": [r["id"] for r in rows if not r["covered"]],
    }


def review(questions: list[Question], policies: Policies, client, model: str, judge: str) -> dict:
    jobs = [(q, how) for q in questions for how in WAYS]
    # The questions are independent, so they run side by side; each graph run is still one at a time.
    with ThreadPoolExecutor(8) as pool:
        rows = list(pool.map(lambda job: one(*job, policies, client, model, judge), jobs))
    by_way = {how: [r for r in rows if r["how"] == how] for how in WAYS}
    return {"rows": rows, **{how: summary(by_way[how], len(questions)) for how in WAYS},
            "grading_cost": round(sum(r["grade_cost"] for r in rows), 6)}
