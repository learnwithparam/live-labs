"""The lab's promise, on a real model and a real Langfuse: the golden set separates a good version from a
bad one, the gate blocks the bad one until a person overrides, a rollback reaches a running bot in under
a minute with no restart, each reply is linked to its version in Langfuse, and an unreachable registry
still leaves the bot a prompt. Needs `docker compose up -d --wait` and LLM_API_KEY."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from driftwell import cli
from driftwell.bot import SupportBot, linked_version
from driftwell.prompt_source import PromptSource
from driftwell.registry import holder, versions
from driftwell.release import ReleaseLog, Refused, Scores, gate, promote

pytestmark = pytest.mark.skipif(not os.environ.get("LLM_API_KEY"), reason="needs LLM_API_KEY")
PROMPTS = Path(__file__).parents[1] / "data" / "prompts"


@pytest.mark.proof
def test_a_bad_prompt_is_blocked_overridden_and_rolled_back_in_under_a_minute():
    assert cli.reset() == 0 and cli.import_prompt() == 0
    assert cli.propose(str(PROMPTS / "sizing-and-export.txt"), "Add the size exchange and the data export path", "Maya Ortiz") == 0
    assert cli.evaluate(again=True) == 0
    assert cli.promote(2, "Maya Ortiz", "") == 0
    assert cli.propose(str(PROMPTS / "warmer-tone.txt"), "Warmer, shorter replies", "Sam Lee") == 0
    # Only the new version is scored; v1 and v2 keep the scores they shipped on.
    assert cli.evaluate(again=False) == 0

    client, scores = cli.registry(), Scores()
    by_number = {version.number: scores.of(version.text) for version in versions(client)}
    assert gate(by_number[2]) is None, "the edited prompt must clear the bar"
    assert gate(by_number[3]) is not None, "the warmer prompt must fall below it"
    # The export path is written only in v2, so the edit, not a lucky reply, is what passes this case.
    download = {number: next(row for row in record["rows"] if row["id"] == "G11") for number, record in by_number.items()}
    assert download[2]["passed"] and not download[1]["passed"]

    with pytest.raises(Refused):
        promote(client, ReleaseLog(), scores, 3, "Sam Lee")
    assert holder(versions(client), "production").number == 2
    assert cli.promote(3, "Sam Lee", "Brand team wants the warmer tone live today") == 0
    assert holder(versions(client), "production").number == 3

    event = cli.watch_rollback("Maya Ortiz")
    assert event["to"] == 2 and event["served_after"] < 60
    assert holder(versions(client), "production").number == 2

    llm, model_name = cli.model()
    answered = SupportBot(PromptSource(client), llm, model_name, tracer=client).reply("Can I export my sleep data?", "Tom")
    client.flush()
    assert answered["version"] == 2 and "export" in answered["reply"].lower()
    assert linked_version(client, answered["trace_id"]) == 2, "Langfuse must link the reply to the version that wrote it"


@pytest.mark.proof
def test_an_unreachable_registry_leaves_the_bot_the_pinned_prompt():
    # A fresh process: the SDK shares one client, and its prompt cache, per key inside a process.
    dead_registry = {**os.environ, "LANGFUSE_BASE_URL": "http://127.0.0.1:8279"}
    asked = subprocess.run([sys.executable, "-m", "driftwell.cli", "ask", "How long does the battery last?"],
                           env=dead_registry, capture_output=True, text=True, check=True)
    assert asked.stdout.startswith("[no version, pinned fallback")
    assert "6 days" in asked.stdout
