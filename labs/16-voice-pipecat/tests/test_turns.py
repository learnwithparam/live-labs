import asyncio
import time
from types import SimpleNamespace

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
)

from pipecat.processors.frame_processor import FrameDirection

from lucca.turns import TurnClock, percentile, split_turn


def test_a_turn_splits_into_hear_think_and_speak_that_add_up_to_the_gap_the_caller_waits():
    parts = split_turn({"speech_end": 10.0, "transcript_at": 10.4, "turn_end_at": 10.6,
                        "first_text_at": 11.3, "audio_at": 11.5})
    assert parts == {"latency_ms": 1500, "hear_ms": 600, "think_ms": 700, "speak_ms": 200}
    assert parts["hear_ms"] + parts["think_ms"] + parts["speak_ms"] == parts["latency_ms"]


def test_percentiles_use_the_nearest_rank_so_p95_of_a_small_sample_is_its_slowest_turn():
    latencies = [900, 1100, 1300, 1500, 1700, 1900, 2100, 4000]
    assert percentile(latencies, 0.5) == 1500
    assert percentile(latencies, 0.95) == 4000
    assert percentile([], 0.5) is None


def push(clock: TurnClock, frame, source=None, direction=FrameDirection.DOWNSTREAM) -> None:
    asyncio.run(clock.on_push_frame(SimpleNamespace(frame=frame, source=source, direction=direction)))


def test_the_clock_times_a_turn_from_the_frames_and_counts_each_frame_once():
    llm, output, events = object(), object(), []
    clock = TurnClock(llm, output, events.append)
    clock.caller_started(1, "a large pepperoni")
    clock.caller_stopped()
    heard = TranscriptionFrame("a large pepperoni", "caller", "now")
    push(clock, heard)
    push(clock, heard)  # the same frame seen at the next hop
    push(clock, LLMFullResponseStartFrame(), llm)
    push(clock, LLMTextFrame("Got it."), llm)
    push(clock, BotStartedSpeakingFrame())
    turn = clock.summary()[0]
    assert turn["heard"] == "a large pepperoni"
    assert turn["latency_ms"] is not None and turn["latency_ms"] >= 0
    assert [event["kind"] for event in events] == ["caller", "turn"]
    # The output announces the stop both ways; the reply reaches the transcript once.
    push(clock, BotStoppedSpeakingFrame(), output, FrameDirection.UPSTREAM)
    push(clock, BotStoppedSpeakingFrame(), output)
    assert [event["kind"] for event in events] == ["caller", "turn", "agent"]
    assert clock.open_replies == 1 and not clock.reply_done(0)
    push(clock, BotStoppedSpeakingFrame())
    push(clock, LLMFullResponseEndFrame(), output)
    clock.quiet_since -= 2  # the line has been quiet long enough
    assert clock.reply_done(0)


def test_a_barge_in_records_how_fast_the_agent_stopped_talking():
    llm, events = object(), []
    clock = TurnClock(llm, object(), events.append)
    push(clock, LLMFullResponseStartFrame(), llm)
    push(clock, LLMTextFrame("That's two small cheese and six barbecue wings"), llm)
    push(clock, BotStartedSpeakingFrame())
    clock.caller_started(3, "make the wings garlic parmesan")
    time.sleep(0.05)
    push(clock, InterruptionFrame())
    turn = clock.summary()[0]
    assert turn["barge_in"] and turn["stop_ms"] >= 50
    assert events[-1]["kind"] == "interrupted" and clock.open_replies == 0
