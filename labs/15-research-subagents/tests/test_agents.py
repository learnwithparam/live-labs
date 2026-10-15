"""The two versions on a scripted model: the budget holds, the subagents really overlap, and the
writer can never change a quote the check reads."""

import asyncio
import json

from fakes import GRANT_QUOTE, FakeModel, reply
from kitebird.lead import MOST_SUBAGENTS, run_lead, run_single
from kitebird.searcher import research


def test_an_agent_that_never_finishes_is_made_to_at_the_budget():
    def keeps_searching(request):
        if request["tool_choice"] != "auto":
            return reply(calls=[("finish", {"findings": []})])
        return reply(calls=[("search", {"query": "cargo bike"})])

    model = FakeModel(research=keeps_searching)
    state = asyncio.run(research("Find grants.", 4, model, "fake", "subagent 1"))
    assert state["status"] == "done"
    assert len(state["queries"]) == 4
    assert model.requests[-1]["tool_choice"] == {"type": "function", "function": {"name": "finish"}}
    # Every result tells the model what is left, so it can plan its reads.
    assert "[0 tool calls left]" in model.requests[-1]["messages"][-1]["content"]


def test_the_subagents_run_at_the_same_time():
    model = FakeModel(plan=["Grants.", "Dealers.", "Rules.", "Shipping.", "Theft.", "Extra."])
    report = asyncio.run(run_lead(model, "fake"))
    searchers = [agent for agent in report["agents"] if agent["agent"].startswith("subagent")]
    assert len(searchers) == MOST_SUBAGENTS
    assert model.most_in_flight == MOST_SUBAGENTS


def test_the_single_agent_runs_alone_and_both_report_the_same_shape():
    model = FakeModel()
    single = asyncio.run(run_single(model, "fake"))
    assert model.most_in_flight == 1
    lead = asyncio.run(run_lead(FakeModel(), "fake"))
    assert single.keys() == lead.keys()
    assert single["usage"]["research"]["tokens"] < lead["usage"]["research"]["tokens"]


def test_duplicate_findings_from_two_subagents_become_one_claim():
    report = asyncio.run(run_lead(FakeModel(), "fake"))
    assert len(report["findings"]) == 1
    claims = [claim for section in report["sections"] for claim in section["claims"]]
    assert [claim["quotes"] for claim in claims] == [[GRANT_QUOTE]]
    assert report["score"]["supported"] == 1
    assert report["score"]["covered"] == ["grants"]
    # Finding numbers are the code's handle on a quote; the reader never sees them.
    assert report["answer"] == "Go." and claims[0]["text"] == "Claim from the finding."


def test_the_writer_cannot_alter_a_quote_and_a_judge_no_goes_to_the_editor():
    def says_no(request):
        # The judge sees the quote the code copied from the finding, not one the writer typed.
        assert f'"{GRANT_QUOTE}"' in request["messages"][1]["content"]
        return reply(json.dumps({"supports": False, "reason": "The source gives no month."}))

    report = asyncio.run(run_single(FakeModel(check=says_no), "fake"))
    claim = report["sections"][0]["claims"][0]
    assert (claim["verdict"], claim["source"]) == ("not supported", "S09")
    assert report["score"]["editor"] == 1
    assert report["score"]["covered"] == []
