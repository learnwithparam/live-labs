"""The lab's promise, on a real model against the order system: a reply-only bot changes nothing, and an
agent loop, ours or Google ADK's, resolves most chats, hands both risky chats to a person, and stays in
budget. The score is read from the order system, never from what the bot said."""

import json
from pathlib import Path

import pytest

from kestrel.cli import review_all


@pytest.mark.proof
def test_an_agent_loop_resolves_chats_a_reply_cannot_and_hands_off_the_risky_ones():
    assert review_all(None) == 0
    got = {engine: r["score"] for engine, r in json.loads(Path("out/review.json").read_text()).items()}
    # A reply can promise a cancellation, but only a tool call moves the order system.
    assert got["reply"]["actions_done"] == 0, got["reply"]
    for engine in ("loop", "adk"):
        s = got[engine]
        assert s["resolved"] >= 6, f"{engine}: {s}"
        assert s["handed_off_right"] == s["should_hand_off"] == 2, f"{engine}: {s}"
        # The budget is a ceiling for a confused chat, not the normal case.
        assert s["over_budget"] == 0, f"{engine}: {s}"
        assert s["resolved"] > got["reply"]["resolved"], f"{engine} did no better than a reply: {s}"
