"""A phone line made of recorded calls, so the proof hears the same callers every run.

A real line delivers audio in small chunks, in real time, with silence between sentences. This transport
does exactly that from committed wav files: the pipeline cannot tell it apart from a microphone, so the
turn detector, the interruptions and the latency clock all run as they would on a live call.
"""

import asyncio
import json
import time
import wave
from pathlib import Path

from pipecat.frames.frames import EndFrame, InputAudioRawFrame, OutputAudioRawFrame, StartFrame
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams

CALLS_FILE = Path("data/calls.json")
AUDIO_DIR = Path("data/calls")
CALLER_RATE = 16000  # phone-quality speech; Whisper and the voice detector both read 16 kHz
CHUNK_SECONDS = 0.02  # a microphone hands over 20 ms at a time
BARGE_IN_AFTER = 1.5  # seconds of the agent's reply a caller hears before cutting in
# A turn that has not answered by now has failed and the call ends there. It is generous because on a
# CPU shared with other work one turn has taken over two minutes; on a quiet laptop it takes seconds.
REPLY_TIMEOUT = 300.0


def load_calls() -> list[dict]:
    return json.loads(CALLS_FILE.read_text())


def line_audio(call_id: str, line_number: int) -> bytes:
    with wave.open(str(AUDIO_DIR / f"{call_id}-{line_number}.wav"), "rb") as recording:
        return recording.readframes(recording.getnframes())


class CallerInput(BaseInputTransport):
    """Feeds the pipeline a steady stream of 20 ms chunks: the caller's speech when there is some queued,
    silence otherwise. The silence matters: the voice detector only knows a caller stopped because it
    keeps hearing quiet audio after they do."""

    def __init__(self, params: TransportParams):
        super().__init__(params)
        self.queued = bytearray()
        self.feeder: asyncio.Task | None = None
        self.drained = asyncio.Event()

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        self.feeder = self.create_task(self.feed())
        await self.set_transport_ready(frame)

    async def stop(self, frame: EndFrame) -> None:
        if self.feeder:
            await self.cancel_task(self.feeder)
        await super().stop(frame)

    async def feed(self) -> None:
        chunk_bytes = int(CALLER_RATE * CHUNK_SECONDS) * 2
        next_tick = time.monotonic()
        while True:
            chunk = bytes(self.queued[:chunk_bytes])
            del self.queued[:chunk_bytes]
            if not self.queued:
                self.drained.set()
            await self.push_audio_frame(InputAudioRawFrame(chunk.ljust(chunk_bytes, b"\0"), CALLER_RATE, 1))
            # Pace by the wall clock, not by sleep alone, so a busy machine does not stretch the call.
            next_tick += CHUNK_SECONDS
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))

    async def say(self, audio: bytes) -> None:
        """Queue one line of speech and return when the last chunk has gone down the line."""
        self.drained.clear()
        self.queued.extend(audio)
        await self.drained.wait()


class CallerOutput(BaseOutputTransport):
    """The caller's earpiece. It takes as long to play a chunk as the chunk lasts, which is what lets a
    caller cut in halfway through a reply."""

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        await asyncio.sleep(len(frame.audio) / 2 / frame.sample_rate / frame.num_channels)
        return True


class RecordedLine(BaseTransport):
    def __init__(self):
        super().__init__()
        params = TransportParams(audio_in_enabled=True, audio_in_sample_rate=CALLER_RATE,
                                 audio_out_enabled=True, audio_out_sample_rate=24000)
        self.caller_in, self.caller_out = CallerInput(params), CallerOutput(params)

    def input(self) -> CallerInput:
        return self.caller_in

    def output(self) -> CallerOutput:
        return self.caller_out


async def play_call(call: dict, line: RecordedLine, clock, on_event=lambda event: None) -> bool:
    """Act out one caller: wait for the greeting, say each line, and wait for the answer before the next.
    A line marked barge_in is said over the agent instead, once the caller has heard enough of the reply
    to the line before it, the way a caller cuts in when the read-back goes wrong."""
    if not await clock.wait_reply(time.monotonic(), REPLY_TIMEOUT):
        return False
    lines = call["lines"]
    for line_number, script_line in enumerate(lines, 1):
        if script_line.get("barge_in"):
            heard_from = clock.turns[-1]["speech_end"]
            if not await clock.wait_speaking(heard_from, BARGE_IN_AFTER, REPLY_TIMEOUT):
                return False
        clock.caller_started(line_number, script_line["say"])
        on_event({"kind": "caller_line", "line": line_number, "said": script_line["say"]})
        await line.caller_in.say(line_audio(call["id"], line_number))
        clock.caller_stopped()
        cut_in_next = line_number < len(lines) and lines[line_number].get("barge_in")
        if not cut_in_next and not await clock.wait_reply(clock.turns[-1]["speech_end"], REPLY_TIMEOUT):
            return False
    return True
