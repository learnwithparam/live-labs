"""The agent loop with a scripted model: the policy's verdict, not the model's good intentions, decides what runs."""

import pytest
from fakes import FakeOpenAI, call

from wrenfield.agent import card, forbidden_ran, reads_blocked, run, unapproved_destructive
from wrenfield.policy import PromptOnly
from wrenfield.workspace import Workspace

ANA = {"id": "ana.costa", "name": "Ana Costa", "role": "intern"}
PRIYA = {"id": "priya.shah", "name": "Priya Shah", "role": "engineer"}
MARCUS = {"id": "marcus.lee", "name": "Marcus Lee", "role": "release_manager"}
DELETE_RELEASE = [call("delete_branch", branch="release/2.4")]


@pytest.fixture
def workspace(tmp_path):
    return Workspace.create(tmp_path / "work")


def test_reads_run_at_once_for_an_intern(policy, workspace):
    model = FakeOpenAI([[call("list_files"), call("read_file", path="README.md"), call("query_db", sql="SELECT 1", environment="prod")],
                        "Read it all."])
    asked = []
    result = run("Look around.", ANA, workspace, policy, model, "openai/gpt-6-luna", approve=lambda request: asked.append(request))
    assert [each["ran"] for each in result["calls"]] == [True, True, True]
    assert reads_blocked(result["calls"]) == 0 and not asked


def test_an_engineer_is_denied_a_protected_branch_and_the_model_is_told_why(policy, workspace):
    model = FakeOpenAI([DELETE_RELEASE, "I could not delete it."])
    result = run("Delete release/2.4.", PRIYA, workspace, policy, model, "openai/gpt-6-luna", approve=lambda request: True)
    denied = result["calls"][0]
    assert denied["verdict"] == "deny" and not denied["ran"]
    assert "release/2.4" in workspace.origin_heads()
    told = model.seen[-1]["messages"][-1]["content"]
    assert told.startswith("Denied by Wrenfield's policy") and "protected" in told.lower()


def test_a_release_manager_gets_a_card_and_a_no_keeps_the_branch(policy, workspace):
    cards = []
    model = FakeOpenAI([DELETE_RELEASE, "Left it."])
    result = run("Delete release/2.4.", MARCUS, workspace, policy, model, "openai/gpt-6-luna",
                 approve=lambda request: cards.append(request) or False)
    assert cards[0]["says"] == "The agent wants to delete branch release/2.4"
    assert cards[0]["reason"] and cards[0]["rule"].startswith("ask-")
    assert result["calls"][0]["approved_by"] == "declined"
    assert "release/2.4" in workspace.origin_heads()


def test_a_yes_on_the_card_runs_it_and_still_counts_zero_unapproved(policy, workspace):
    model = FakeOpenAI([DELETE_RELEASE, "Deleted."])
    result = run("Delete release/2.4.", MARCUS, workspace, policy, model, "openai/gpt-6-luna", approve=lambda request: True)
    assert result["calls"][0]["approved_by"] == "card" and result["calls"][0]["ran"]
    assert unapproved_destructive(result["calls"]) == 0
    assert "release/2.4" not in workspace.origin_heads()


def test_prompt_only_runs_a_destructive_call_with_nobody_asked(policy, workspace):
    model = FakeOpenAI([DELETE_RELEASE, "Done."])
    result = run("Delete release/2.4.", PRIYA, workspace, PromptOnly(policy), model, "openai/gpt-6-luna")
    assert unapproved_destructive(result["calls"]) == 1
    assert forbidden_ran(result["calls"]) == 1
    assert "release/2.4" not in workspace.origin_heads()


def test_a_chat_yes_counts_as_approval_but_not_as_permission(policy, workspace):
    model = FakeOpenAI(["release/2.4 is protected. Do you want me to delete it anyway?", DELETE_RELEASE, "Deleted."])
    result = run("Delete release/2.4.", PRIYA, workspace, PromptOnly(policy), model, "openai/gpt-6-luna",
                 answer=lambda reply: "Yes, go ahead.")
    assert result["calls"][0]["approved_by"] == "chat"
    assert unapproved_destructive(result["calls"]) == 0
    # The engineer said yes, but the policy says only a release manager may: the shadow verdict shows it.
    assert forbidden_ran(result["calls"]) == 1


def test_a_reply_that_asks_nothing_ends_the_run_without_a_follow_up(policy, workspace):
    answered = []
    model = FakeOpenAI(["All tests pass."])
    result = run("Run the tests.", PRIYA, workspace, policy, model, "openai/gpt-6-luna", answer=lambda reply: answered.append(reply))
    assert result["follow_up"] is None and not answered


def test_the_card_says_where_a_database_change_lands(policy):
    decision = policy.decide(MARCUS, "run_migration", {"name": "0008_drop_legacy_rates", "environment": "prod"})
    says = card(MARCUS, "run_migration", {"name": "0008_drop_legacy_rates", "environment": "prod"}, decision)["says"]
    assert says == "The agent wants to run migration 0008_drop_legacy_rates on prod"


def test_cost_comes_from_the_tokens_the_provider_reports(policy, workspace):
    model = FakeOpenAI(["Nothing to do."], tokens=(10_000, 1_000))
    result = run("Hi.", PRIYA, workspace, policy, model, "openai/gpt-6-luna")
    assert result["usage"]["cost"] == pytest.approx((10_000 * 0.10 + 1_000 * 0.50) / 1e6)
