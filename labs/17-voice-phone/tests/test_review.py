"""The verdict reads only the booking system and the handoff queue, so a confident agent that changed
the wrong visit still misses. The caller picks its next line from what the agent actually said."""

from northfield.caller import next_line, scripts
from northfield.review import judge, score


def visit(visit_id="V-2050", kind="tune-up", window="WED-PM", status="booked"):
    return {"id": visit_id, "kind": kind, "window": window, "status": status}


def result(visits=(), handoffs=(), customers=(), agent_said=""):
    return {"changes": {"visits": list(visits), "handoffs": list(handoffs), "customers": list(customers)},
            "turns": [{"who": "agent", "text": agent_said}]}


def test_ten_callers_expect_seven_resolutions_and_three_transfers():
    outcomes = [script["expect"]["outcome"] for script in scripts().values()]
    assert len(outcomes) == 10 and outcomes.count("transferred") == 3


def test_a_booking_counts_only_in_the_right_window():
    expect = {"outcome": "booked", "kind": "tune-up", "window": "WED-PM"}
    assert judge(expect, result([visit()]))[0]
    assert not judge(expect, result([visit(window="THU-PM")]))[0]
    assert not judge(expect, result())[0]


def test_a_transfer_counts_only_with_the_right_reason_and_no_booking():
    expect = {"outcome": "transferred", "reason": "safety"}
    assert judge(expect, result(handoffs=[{"reason": "safety"}]))[0]
    assert not judge(expect, result(handoffs=[{"reason": "other"}]))[0]
    assert not judge(expect, result([visit()], [{"reason": "safety"}]))[0]


def test_sending_a_resolvable_call_to_a_person_is_a_miss():
    expect = {"outcome": "answered", "says": ["eighty-nine"]}
    assert judge(expect, result(agent_said="A tune-up is eighty-nine dollars."))[0]
    assert not judge(expect, result(handoffs=[{"reason": "other"}], agent_said="eighty-nine"))[0]


def test_the_score_counts_reached_calls_and_transfers_separately():
    calls = [{"id": "NF-01", "reached": True, "transfer": None, "resolved_without_person": True,
              "talked_over": 0, "cost": 0.001, "seconds": 90},
             {"id": "NF-05", "reached": True, "transfer": {"reason": "safety"}, "resolved_without_person": False,
              "talked_over": 1, "cost": 0.001, "seconds": 60},
             {"id": "NF-09", "reached": False, "transfer": None, "resolved_without_person": False,
              "talked_over": 0, "cost": 0.001, "seconds": 70}]
    tally = score(calls)
    assert (tally["reached"], tally["share"], tally["resolved_without_person"]) == (2, 0.67, 1)
    assert tally["transfers"] == [{"id": "NF-05", "reason": "safety"}]


def test_the_caller_skips_ahead_when_the_agent_asks_for_its_line():
    lines = scripts()["NF-07"]["lines"]
    assert next_line(lines, 0, "Can I get your name and address?") == 1
    assert next_line(lines, 0, "Shall I book Friday afternoon?") == 2
    assert next_line(lines, len(lines) - 1, "Goodbye") is None


def test_the_caller_opens_with_its_first_line_whatever_the_greeting_says():
    lines = scripts()["NF-08"]["lines"]
    assert next_line(lines, -1, "How can I help?") == 0
