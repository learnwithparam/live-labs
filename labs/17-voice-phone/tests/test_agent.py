"""The front desk's tools against the real booking system, driven by a scripted model in text mode.

AgentSession runs without audio or a room here, so these tests check the part a phone line cannot
show: what each tool call writes into the book, and that a transfer leaves the reason a person needs."""

import asyncio
import json

from fakes import ScriptedLLM, tool_call
from livekit.agents import AgentSession

from northfield.agent import GREETING, INSTRUCTIONS, CallLog, FrontDesk, turn_handling
from northfield.schedule import Schedule, changes, original

SANDRA = "+16145550101"
MARCUS = "+16145550178"


def talk(tmp_path, number: str, replies: list, caller_says: str):
    """One caller turn in text mode: the session runs the scripted replies and every tool they call."""
    book = Schedule(tmp_path / "schedule.json")
    log = CallLog(tmp_path, "TEST", number)

    async def conversation() -> None:
        async with AgentSession(llm=ScriptedLLM(replies)) as session:
            await session.start(FrontDesk(book, number, log))
            await session.run(user_input=caller_says)

    asyncio.run(conversation())
    return book, log


def test_a_booking_lands_in_the_book_and_the_call_log(tmp_path):
    replies = [[tool_call("find_windows")],
               [tool_call("book_visit", kind="tune-up", window_id="WED-PM", problem="yearly furnace tune-up")],
               "You're booked for Wednesday afternoon."]
    book, log = talk(tmp_path, SANDRA, replies, "Yes, book the Wednesday afternoon tune-up.")
    changed = changes(original(), book.book_of_record)
    assert [(visit["kind"], visit["window"], visit["phone"]) for visit in changed["visits"]] == [("tune-up", "WED-PM", SANDRA)]
    assert [tool["name"] for tool in log.state["tools"]] == ["find_windows", "book_visit"]
    assert json.loads((tmp_path / "status.json").read_text())["tools"][1]["result"]["ok"] is True


def test_a_full_window_is_refused_and_nothing_is_booked(tmp_path):
    replies = [[tool_call("book_visit", kind="repair", window_id="TUE-PM", problem="no heat")], "That window is full."]
    book, log = talk(tmp_path, SANDRA, replies, "Tuesday afternoon please.")
    assert changes(original(), book.book_of_record)["visits"] == []
    assert "full" in log.state["tools"][0]["result"]["reason"]


def test_a_caller_cannot_move_someone_elses_visit(tmp_path):
    replies = [[tool_call("move_visit", visit_id="V-2041", window_id="THU-PM")], "I can't find that visit."]
    book, _ = talk(tmp_path, SANDRA, replies, "Move V-2041 to Thursday.")
    assert changes(original(), book.book_of_record)["visits"] == []


def test_a_transfer_files_the_reason_and_summary_for_the_person(tmp_path):
    summary = "Marcus Bell smells gas at home and has left the house."
    replies = [[tool_call("transfer_to_human", reason="safety", summary=summary)], "Connecting you to the dispatcher now."]
    book, _ = talk(tmp_path, MARCUS, replies, "I smell gas in the basement.")
    handed = changes(original(), book.book_of_record)["handoffs"]
    assert handed == [{"phone": MARCUS, "reason": "safety", "summary": summary}]


def test_the_caller_card_comes_from_caller_id_not_from_the_model(tmp_path):
    book = Schedule(tmp_path / "schedule.json")
    assert "Marcus Bell" in book.caller_card(MARCUS) and "V-2041" in book.caller_card(MARCUS)
    assert "not a customer yet" in book.caller_card("+16145550199")


def test_the_prompt_discloses_nothing_it_cannot_back_and_the_greeting_discloses_ai_and_recording():
    assert "automated" in GREETING and "recorded" in GREETING
    assert "transfer_to_human" in INSTRUCTIONS and "end_call" in INSTRUCTIONS
    # Silence alone would cut a caller off between sentences; the detector waits at least this long.
    assert turn_handling()["endpointing"]["min_delay"] >= 0.5
