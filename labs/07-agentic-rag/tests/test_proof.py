"""The lab's promise, against a real model: on the expert's 16 questions the agent's answers are accepted
far more often than plain RAG's, because it finds the sections a question needs but does not name."""

from pathlib import Path

import pytest

from policy.cli import client_and_models
from policy.grade import load
from policy.ingest import ingest
from policy.review import review
from policy.search import Policies

LAB = Path(__file__).parent.parent
MIN_ACCEPTED = 14  # of 16 agent answers the expert's points must accept
MIN_FOUND = 30  # of the 33 sections the expert's answers use, in front of the agent's model


@pytest.mark.proof
def test_the_agent_is_accepted_more_often_than_plain_rag():
    client, model, judge = client_and_models()
    got = review(load(LAB / "data/expert.jsonl"), Policies(ingest(LAB / "data/docs")), client, model, judge)
    agent, base = got["agentic"], got["plain"]
    assert agent["accepted"] >= MIN_ACCEPTED, f"{model}: {agent}"
    assert agent["accepted"] > base["accepted"], f"agent {agent['accepted']}, plain {base['accepted']}"
    assert agent["found"] >= MIN_FOUND and agent["found"] > base["found"], f"agent {agent['found']}, plain {base['found']}"
