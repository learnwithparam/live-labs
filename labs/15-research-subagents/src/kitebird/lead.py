"""Two ways to research one question: a single agent, or a lead agent with subagents in parallel.

Anthropic's research system has a lead agent plan the work and hand each part to a subagent with its
own context window. The subagents search at the same time, so breadth costs tokens, not wall-clock
time. Both versions end the same way: one writer turns the findings into a report, then every claim is
checked against the source it cites.
"""

import asyncio
import json
import re
import time
import uuid
from typing import Callable

from kitebird.citations import check_report
from kitebird.corpus import Library, load_brief
from kitebird.searcher import add_usage, research, usage_of

# Every agent gets the same budget, one tool call per model turn, so both versions take about as long.
# The lead buys breadth with more agents in parallel, which costs tokens, not time.
SINGLE_BUDGET = 10
SUBAGENT_BUDGET = 10
MOST_SUBAGENTS = 4
FINDING_NUMBERS = re.compile(r"\s*[(\[]F\d+(?:[,;\s]+F\d+)*[)\]]")

PLAN = """Plan research for the question below. Split it into at most {most} objectives that do not overlap, one per subagent.
Each subagent has {budget} tool calls, enough to search and read about four sources, so keep each objective that small.
Each objective names what to find, the kind of sources to prefer, and where its scope ends.
Return JSON: {{"objectives": ["...", "..."]}}"""

WRITE = """Write a short research brief that answers the question from the numbered findings only.
Return JSON: {"title": "...", "answer": "two or three sentences with the recommendation",
"sections": [{"heading": "...", "claims": [{"text": "...", "finding": "F3"}]}]}
- Every claim cites exactly one finding by its number and says no more than that finding says.
- Where findings disagree, write a claim for each side and say which source is newer.
- Leave out findings that do not help answer the question.
- Never write a claim about what the findings leave out: a gap is not a fact a source can support."""


def unnumbered(text: str) -> str:
    return FINDING_NUMBERS.sub("", text).strip()


def question_text(brief: dict) -> str:
    wants = "; ".join(aspect["label"].lower() for aspect in brief["aspects"])
    return f"{brief['question']} {brief['asked_by']} wants a cited brief that covers: {wants}."


async def ask_json(client, model: str, system: str, user: str, usage: dict) -> dict:
    """One JSON reply. A reply that is not JSON reads as empty, so a bad plan never crashes the run."""
    response = await client.chat.completions.create(
        model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"}, extra_body={"usage": {"include": True}})
    add_usage(usage, usage_of(response, model))
    try:
        return json.loads(response.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        return {}


async def write_report(question: str, findings: list[dict], client, model: str, usage: dict) -> dict:
    """The writer cites findings by number and the code copies the quotes across, so the writer can
    reword a claim but can never alter the quotes that the check reads."""
    numbered = {f"F{number}": finding for number, finding in enumerate(findings, 1)}
    listing = "\n".join(f"{key} ({finding['source']}): {finding['claim']} Quotes: " + " ".join(f'"{quote}"' for quote in finding["quotes"])
                        for key, finding in numbered.items())
    draft = await ask_json(client, model, WRITE, f"Question: {question}\n\nFindings:\n{listing}", usage)
    # Finding numbers are for the code, not the reader; a writer that leaves "(F3)" in the prose gets it removed.
    sections = []
    for section in draft.get("sections") or []:
        claims = []
        for claim in section.get("claims") or []:
            finding = numbered.get(str(claim.get("finding", "")).strip())
            claims.append({"text": unnumbered(str(claim.get("text", ""))), "finding": claim.get("finding"),
                           "source": finding["source"] if finding else None, "quotes": finding["quotes"] if finding else []})
        sections.append({"heading": str(section.get("heading", "")), "claims": claims})
    return {"title": str(draft.get("title", "")), "answer": unnumbered(str(draft.get("answer", ""))), "sections": sections}


def merge(results: list[dict]) -> list[dict]:
    """Two subagents that found the same quotes add one finding, not two."""
    seen, merged = set(), []
    for result in results:
        for finding in result["findings"]:
            key = (finding["source"].upper(), tuple(" ".join(quote.lower().split()) for quote in finding["quotes"]))
            if key not in seen:
                seen.add(key)
                merged.append(finding)
    return merged


async def run_lead(client, model: str, on_event: Callable[[dict], None] = lambda state: None,
                   brief: dict | None = None, library: Library | None = None) -> dict:
    brief, library, started = brief or load_brief(), library or Library(), time.monotonic()
    question = question_text(brief)
    lead = {"agent": "lead", "objective": question, "status": "planning", "queries": [], "reads": [],
            "tool_calls": 0, "findings": [], "usage": {}}
    on_event(lead)
    plan = await ask_json(client, model, PLAN.format(most=MOST_SUBAGENTS, budget=SUBAGENT_BUDGET), question, lead["usage"])
    objectives = [str(objective) for objective in plan.get("objectives") or [] if str(objective).strip()][:MOST_SUBAGENTS] or [question]
    lead["status"] = "waiting"
    lead["objectives"] = objectives
    on_event(lead)
    # The subagents run at the same time: gather starts every loop before awaiting any of them.
    results = await asyncio.gather(*(research(objective, SUBAGENT_BUDGET, client, model, f"subagent {number}", on_event, library)
                                     for number, objective in enumerate(objectives, 1)))
    findings = merge(results)
    lead["status"] = "writing"
    on_event(lead)
    report = await write_report(question, findings, client, model, lead["usage"])
    return await finish("lead", brief, report, findings, [lead, *results], client, model, on_event, started, library)


async def run_single(client, model: str, on_event: Callable[[dict], None] = lambda state: None,
                     brief: dict | None = None, library: Library | None = None) -> dict:
    brief, library, started = brief or load_brief(), library or Library(), time.monotonic()
    question = question_text(brief)
    # One context window does everything: every search result and source it reads stays in it.
    agent = await research(question, SINGLE_BUDGET, client, model, "single agent", on_event, library)
    agent["status"] = "writing"
    on_event(agent)
    report = await write_report(question, agent["findings"], client, model, agent["usage"])
    return await finish("single", brief, report, agent["findings"], [agent], client, model, on_event, started, library)


async def finish(version: str, brief: dict, report: dict, findings: list[dict], agents: list[dict], client, model: str,
                 on_event: Callable[[dict], None], started: float, library: Library) -> dict:
    """Check every claim, then total the bill. The check is billed apart, so the research cost is clear."""
    checker = {"agent": "citation check", "objective": "Check every claim against the source it cites.",
               "status": "checking", "queries": [], "reads": [], "tool_calls": 0, "findings": [], "usage": {}}
    on_event(checker)
    score = await check_report(report, brief, library.sources, client, model, checker["usage"])
    checker["status"] = "done"
    for agent in agents:
        agent["status"] = "done"
        on_event(agent)
    on_event(checker)
    research_usage = {}
    for agent in agents:
        add_usage(research_usage, agent["usage"])
    return {"id": uuid.uuid4().hex[:8], "version": version, "model": model, "question": brief["question"], "asked_by": brief["asked_by"],
            **report, "findings": findings, "agents": agents, "score": score, "seconds": round(time.monotonic() - started, 1),
            "usage": {"research": research_usage, "check": checker["usage"]}}
