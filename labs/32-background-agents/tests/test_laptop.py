"""The baseline, keyless: on a laptop the agent's own shell reaches the planted token and credentials file."""

import os

from fakes import SCRIPTS, ScriptedModel

from fernhill.agent import Workspace
from fernhill.cli import issues
from fernhill.config import PLANTED_SECRET
from fernhill.laptop import fresh_checkout, laptop_run, plant, probe


def test_on_a_laptop_the_agents_shell_reaches_the_planted_secrets(tmp_path, monkeypatch):
    for name, value in plant(tmp_path / "home").items():
        monkeypatch.setenv(name, value)
    reachable = probe(Workspace(fresh_checkout(tmp_path / "checkout")).shell)
    assert reachable["deploy token in the environment"]["reached"] is True
    assert reachable["credentials file in the home folder"]["reached"] is True


def test_the_file_tools_stay_in_the_checkout_but_that_is_not_isolation(tmp_path):
    workspace = Workspace(fresh_checkout(tmp_path / "checkout"))
    assert workspace.call("read_file", {"path": "../../etc/hosts"}).startswith("error: ")
    # The shell has no such rule: it reads what the user running it can read.
    secret = tmp_path / "outside.txt"
    secret.write_text(PLANTED_SECRET)
    assert PLANTED_SECRET in workspace.call("run_shell", {"command": f"cat {secret}"})


def test_laptop_run_works_issues_one_after_another_and_counts_the_blocked_time(tmp_path):
    home = os.environ["HOME"]
    report = laptop_run(issues(["FH-110"]), ScriptedModel(SCRIPTS["refuse"]), "scripted", tmp_path, emit=lambda line: None)
    assert [run["status"] for run in report["runs"]] == ["gave_up"]
    assert report["probes"]["deploy token in the environment"]["reached"] is True
    assert report["blocked_seconds"] >= 0 and report["cost"] == 0.001
    # The planted environment went to the agent's shell only: this process, and the docker CLI, keep theirs.
    assert os.environ["HOME"] == home and "FERNHILL_DEPLOY_TOKEN" not in os.environ
