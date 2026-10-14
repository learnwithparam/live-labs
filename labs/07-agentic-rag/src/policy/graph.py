"""Agentic RAG as a LangGraph graph: plan the lookups, search, check what is still missing, answer.

Plain RAG searches once with the engineer's words. Policy questions often need two documents that
share no words with the question: "roll back on Friday" needs the runbook and the freeze rule. The
agent writes its own searches, then reads what it found and searches again for the part still missing.

    plan -> search -> check --enough--> answer
                ^        |
                +--missing (at most MAX_ROUNDS searches)
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from policy.answer import write
from policy.llm import ask
from policy.search import Policies, listing

# Each extra round is two model calls and seconds of latency; a budget keeps a lost agent from looping.
MAX_ROUNDS = 3

PLAN = (
    "You plan searches over Ledgerline's on-call runbooks and policies (deploy policy, incident severity, "
    "access and secrets, card data, on-call handbook, and a runbook per service). Split the question into "
    "the separate things to look up, one to four. Write each as a short search in the words a runbook "
    "would use: the service name, the alert, the rule (for example 'deploy freeze window weekend'). Add a "
    "search for each policy the situation triggers even when the question does not name it: slowness or an "
    "outage needs incident severity and paging; any change in the evening or at the weekend needs the "
    "deploy freeze; a secret or card number in the wrong place needs the access or card data rules."
)
CHECK = (
    "You check whether the sections found so far answer every part of an on-call engineer's question "
    "about Ledgerline's policies. A part is answered only if a section states it. Think about rules that "
    "apply even though the question does not name them: a deploy freeze, a severity level, who must "
    "approve. When a found section already holds the rule, the part is answered, even if applying it "
    "takes thought. Only if a rule or fact is not in any section, write one to two new short searches "
    "for it in runbook words."
)


class Plan(BaseModel):
    queries: list[str]


class Check(BaseModel):
    enough: bool
    missing: str
    queries: list[str]


def merge(old: list[str], new: list[str]) -> list[str]:
    """The sources reducer: keep every section found in any round, once, in the order found."""
    return old + [i for i in new if i not in old]


class State(TypedDict, total=False):
    question: str
    queries: list[str]
    # Annotated fields are reducers: each node returns only what it adds, and LangGraph merges it in.
    sources: Annotated[list[str], merge]
    searches: Annotated[list[dict], operator.add]
    rounds: int
    fresh: int
    missing: str
    answer: str
    cited: list[str]
    covered: bool
    cost: Annotated[float, operator.add]
    calls: Annotated[int, operator.add]


def build(policies: Policies, client, model: str):
    def plan(state: State) -> State:
        got, cost = ask(client, model, PLAN, state["question"], Plan)
        # A plan that did not parse falls back to plain RAG's one search, never to no search.
        queries = (got.queries if got and got.queries else [state["question"]])[:4]
        return {"queries": queries, "rounds": 0, "cost": cost, "calls": 1}

    def search(state: State) -> State:
        searches = [{"query": q, "found": [p.id for p in policies.search(q)]} for q in state["queries"]]
        new = [i for s in searches for i in s["found"] if i not in state["sources"]]
        return {"searches": searches, "sources": new, "fresh": len(new), "rounds": state["rounds"] + 1}

    def check(state: State) -> State:
        if state["rounds"] > 1 and not state["fresh"]:
            # A retry that found no new section will not find one next time either: answer with what there is.
            return {"queries": [], "missing": "", "calls": 0}
        found = listing(policies.get(state["sources"]))
        got, cost = ask(client, model, CHECK, f"Sections found:\n\n{found}\n\nQuestion: {state['question']}", Check)
        if got is None or got.enough or not got.queries:
            return {"queries": [], "missing": "", "cost": cost, "calls": 1}
        return {"queries": got.queries[:2], "missing": got.missing, "cost": cost, "calls": 1}

    def answer(state: State) -> State:
        result, cost = write(state["question"], state["sources"], policies, client, model)
        return result | {"cost": cost, "calls": 1}

    def route(state: State) -> str:
        # The loop's exit is code, not the model's say-so: new searches and budget left, or answer now.
        return "search" if state["queries"] and state["rounds"] < MAX_ROUNDS else "answer"

    graph = StateGraph(State)
    graph.add_node("plan", plan)
    graph.add_node("search", search)
    graph.add_node("check", check)
    graph.add_node("answer", answer)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "search")
    graph.add_edge("search", "check")
    graph.add_conditional_edges("check", route, {"search": "search", "answer": "answer"})
    graph.add_edge("answer", END)
    return graph.compile()


def start(question: str) -> State:
    return {"question": question, "sources": [], "searches": [], "cost": 0.0, "calls": 0}


def agentic(question: str, policies: Policies, client, model: str) -> dict:
    """Run the graph to the end and return its final state."""
    return dict(build(policies, client, model).invoke(start(question)))
