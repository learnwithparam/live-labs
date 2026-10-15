"""The lab's promise, on a real LLM and the real decision model: on this morning's 72 tickets the
LLM's own confidence sends almost nothing to a person, the decision model's probability catches wrong
picks, and with both, few tickets land in a queue that has to move them."""

import pytest

from triage.cli import routers, stop
from triage.review import review
from triage.tickets import inbox

MAX_REASSIGNED = 5  # of 72 tickets routed by LLM + decision that an agent has to move
MAX_TO_PERSON = 18  # of 72 the triage rota routes by hand: a quarter of the inbox at most


@pytest.mark.proof
def test_the_second_opinion_keeps_reassignments_low_and_the_llm_alone_cannot_doubt():
    built = routers(("llm", "decision"))
    try:
        got = review(inbox(), {how: built[how] for how in ("llm", "decision")})
    finally:
        stop(built)
    llm, decision, both = got["llm"], got["decision"], got["both"]
    assert llm["to_person"] <= 3, f"the LLM's own confidence sent {llm['to_person']} to a person: {llm}"
    assert decision["caught"] >= 1, f"the decision model's probability caught no wrong pick: {decision}"
    assert both["reassigned"] <= MAX_REASSIGNED and both["to_person"] <= MAX_TO_PERSON, f"{both}"
    assert decision["cost_per_1000"] == 0
