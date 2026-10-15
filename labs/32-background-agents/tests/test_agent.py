"""The agent loop on its own: it stops on finish, give_up or a refused endpoint, and never loops forever."""

from fakes import SCRIPTS, ScriptedModel

from fernhill.agent import OUTPUT_LIMIT, Workspace, clip, run_agent
from fernhill.cli import issues
from fernhill.laptop import fresh_checkout

ISSUE = issues(["FH-101"])[0]


def test_a_402_from_the_proxy_ends_the_session_with_the_reason(tmp_path):
    events = []
    outcome = run_agent(ISSUE, Workspace(fresh_checkout(tmp_path / "repo")), ScriptedModel(SCRIPTS["fix"], refuse_after=1),
                        "scripted", emit=events.append)
    assert outcome["status"] == "stopped" and "402" in outcome["reason"]
    assert events[-1]["type"] == "outcome"


def test_a_model_that_never_calls_finish_stops_at_the_turn_limit(tmp_path):
    outcome = run_agent(ISSUE, Workspace(fresh_checkout(tmp_path / "repo")), ScriptedModel([]), "scripted", max_turns=3)
    assert outcome == {"status": "stopped", "reason": "no finish after 3 turns", "turns": 3, "cost": 0.003}


def test_long_tool_output_keeps_the_end_where_the_failure_is():
    text = "start " + "x" * OUTPUT_LIMIT + " AssertionError at the end"
    assert clip(text).endswith("AssertionError at the end") and len(clip(text)) < len(text)


def test_edit_file_needs_text_that_appears_exactly_once(tmp_path):
    workspace = Workspace(fresh_checkout(tmp_path / "repo"))
    answer = workspace.call("edit_file", {"path": "invoices/money.py", "old_text": "return", "new_text": "yield"})
    assert answer.startswith("old_text appears") and "must appear once" in answer
