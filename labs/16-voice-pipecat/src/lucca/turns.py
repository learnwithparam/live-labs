"""The latency budget, measured per turn by watching frames move through the pipeline.

A caller judges the line by one gap: from the moment they stop talking to the first sound of the reply.
That gap is the sum of three waits, so each turn records all three:

- hear: the caller's audio becomes text, and the turn detector decides the caller has finished
- think: the model streams its first spoken word, after any tool calls it makes on the way
- speak: text-to-speech turns that first sentence into the first audio the caller hears
"""

import asyncio
import time
from collections.abc import Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection

QUIET_SECONDS = 1.0  # how long the line must stay silent before a polite caller speaks again


def percentile(values: list[float], share: float) -> float | None:
    """Nearest-rank percentile. With eight calls a p95 is the slowest turn or close to it, which is the
    honest reading of a small sample."""
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, -(-int(share * 100) * len(ordered) // 100))
    return ordered[rank - 1]


def split_turn(turn: dict) -> dict:
    """Turn the timestamps of one turn into the three waits, in milliseconds."""
    ended, audio = turn["speech_end"], turn.get("audio_at")
    if audio is None:
        return {"latency_ms": None, "hear_ms": None, "think_ms": None, "speak_ms": None}
    heard = max(time_point for time_point in (turn.get("transcript_at"), turn.get("turn_end_at"), ended) if time_point)
    first_word = turn.get("first_text_at") or heard
    heard, first_word = min(heard, audio), min(max(first_word, heard), audio)
    return {"latency_ms": round((audio - ended) * 1000), "hear_ms": round((heard - ended) * 1000),
            "think_ms": round((first_word - heard) * 1000), "speak_ms": round((audio - first_word) * 1000)}


class TurnClock(BaseObserver):
    """Watches every frame the pipeline pushes. Pipecat calls an observer for each hop, so a frame is
    counted once, by its id, the first time it is seen."""

    def __init__(self, llm, output, on_event: Callable[[dict], None] = lambda event: None, **kwargs):
        super().__init__(**kwargs)
        self.llm, self.output, self.on_event = llm, output, on_event
        self.seen: set[int] = set()
        self.turns: list[dict] = []
        self.bot_speaking, self.open_replies, self.tools_running = False, 0, 0
        self.bot_started_at = self.quiet_since = time.monotonic()
        self.bot_text = ""

    # The caller's side: the transport says when each line of speech starts and ends.
    def caller_started(self, line: int, said: str) -> None:
        if self.bot_speaking:
            self.turns and self.turns[-1].update(barged_in=True)
        self.turns.append({"line": line, "said": said, "speech_start": time.monotonic(), "heard": "",
                           "barge_in": self.bot_speaking})

    def caller_stopped(self) -> None:
        self.turns[-1]["speech_end"] = time.monotonic()

    def current(self) -> dict | None:
        return self.turns[-1] if self.turns else None

    async def on_push_frame(self, data: FramePushed) -> None:
        frame, now = data.frame, time.monotonic()
        # The reply's end counts only when the output pushes it, after its last audio has played.
        if isinstance(frame, LLMFullResponseEndFrame) and data.source is self.output:
            self.open_replies = max(0, self.open_replies - 1)
            self.quiet_since = now
            return
        if frame.id in self.seen:
            return
        # The output tells the processors on both sides when the agent starts and stops talking, as two
        # frames. Reading only the copy that travels on keeps each reply in the transcript once.
        if isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)) and data.direction != FrameDirection.DOWNSTREAM:
            return
        self.seen.add(frame.id)
        turn = self.current()
        if isinstance(frame, TranscriptionFrame) and turn:
            turn["heard"] = f"{turn['heard']} {frame.text}".strip()
            turn.setdefault("transcript_at", now)
            self.on_event({"kind": "caller", "text": turn["heard"], "line": turn["line"]})
        elif isinstance(frame, UserStoppedSpeakingFrame) and turn and "speech_end" in turn:
            turn.setdefault("turn_end_at", now)
        elif isinstance(frame, LLMFullResponseStartFrame) and data.source is self.llm:
            self.open_replies += 1
            self.bot_text = ""
        elif isinstance(frame, LLMTextFrame) and data.source is self.llm:
            if turn and "speech_end" in turn:
                turn.setdefault("first_text_at", now)
            self.bot_text += frame.text
        elif isinstance(frame, FunctionCallInProgressFrame):
            self.tools_running += 1
        elif isinstance(frame, FunctionCallResultFrame):
            self.tools_running = max(0, self.tools_running - 1)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self.bot_speaking, self.bot_started_at = True, now
            if turn and "speech_end" in turn and "audio_at" not in turn:
                turn["audio_at"] = now
                turn.update(split_turn(turn))
                self.on_event({"kind": "turn", **self.public(turn)})
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self.bot_speaking, self.quiet_since = False, now
            self.on_event({"kind": "agent", "text": self.bot_text.strip()})
        elif isinstance(frame, InterruptionFrame):
            # A barge-in drops the reply in flight, so its end frame never reaches the output.
            if turn and turn.get("barge_in") and "stop_ms" not in turn:
                turn["stop_ms"] = round((now - turn["speech_start"]) * 1000)
                self.on_event({"kind": "interrupted", "text": self.bot_text.strip(), "stop_ms": turn["stop_ms"]})
            self.open_replies = 0

    def reply_done(self, since: float) -> bool:
        """The agent has answered and gone quiet: it spoke after `since`, no reply or tool is still in
        flight, and the line has been silent for QUIET_SECONDS."""
        idle = not self.bot_speaking and self.open_replies == 0 and self.tools_running == 0
        return self.bot_started_at > since and idle and time.monotonic() - self.quiet_since >= QUIET_SECONDS

    async def wait_reply(self, since: float, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.reply_done(since):
                return True
            await asyncio.sleep(0.05)
        return False

    async def wait_speaking(self, since: float, seconds_in: float, timeout: float) -> bool:
        """For a barge-in: wait until the agent has been talking for `seconds_in`, the way a caller cuts
        in once they hear the read-back go wrong."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.bot_speaking and self.bot_started_at > since and time.monotonic() - self.bot_started_at >= seconds_in:
                return True
            await asyncio.sleep(0.02)
        return False

    @staticmethod
    def public(turn: dict) -> dict:
        keys = ("line", "said", "heard", "barge_in", "stop_ms", "latency_ms", "hear_ms", "think_ms", "speak_ms")
        return {key: turn.get(key) for key in keys}

    def summary(self) -> list[dict]:
        return [self.public(turn) for turn in self.turns]
