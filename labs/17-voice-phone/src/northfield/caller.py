"""A simulated caller: joins the call's room the way a SIP trunk would and plays a recorded script.

The caller never sees the agent's tools or the booking system. It hears the agent through LiveKit's
transcript stream, picks its next line by keywords in what the agent just said, waits for the agent to
finish before it speaks, and counts every time the agent starts talking over it."""

import asyncio
import json
import time
import wave
from pathlib import Path

import numpy as np
from livekit import api, rtc

from northfield.config import DATA, livekit_settings
from northfield.phone_audio import PHONE_RATE

FRAME = PHONE_RATE // 50  # 20 ms frames, the size a SIP gateway sends
SETTLE = 1.0  # seconds the agent must stay listening before the caller speaks, so tool calls finish
QUEUE_MS = 30_000  # room for the longest line, queued whole
PATIENCE = 60  # seconds the caller waits for an answer before hanging up


def scripts() -> dict[str, dict]:
    return {script["id"]: script for script in json.loads((DATA / "calls.json").read_text())}


def line_audio(call_id: str, number: int) -> Path:
    return DATA / "calls" / f"{call_id}-{number}.wav"


def next_line(lines: list[dict], said: int, agent_words: str) -> int | None:
    """The first unsaid line whose keywords the agent just used, else the next line in order."""
    if said < 0:
        # The opening line always answers the greeting. "How can I help?" once matched a later line's
        # "help" cue, and the caller said goodbye before it asked its question.
        return 0
    heard = agent_words.lower()
    for index in range(said + 1, len(lines)):
        if any(keyword in heard for keyword in lines[index].get("when", [])):
            return index
    return said + 1 if said + 1 < len(lines) else None


def caller_token(script: dict, room_name: str) -> str:
    # Kind "sip" and the sip.phoneNumber attribute are what a real SIP trunk gives an inbound caller.
    _, key, secret = livekit_settings()
    return (api.AccessToken(key, secret)
            .with_identity(f"sip_{script['number']}").with_name(script["caller"]).with_kind("sip")
            .with_attributes({"sip.phoneNumber": script["number"], "sip.callStatus": "active"})
            .with_grants(api.VideoGrants(room_join=True, room=room_name)).to_jwt())


class Ear:
    """What the caller can observe: the agent's state, its finished sentences, and whether the line is up."""

    def __init__(self):
        self.state, self.spoke, self.sentences, self.connected = "initializing", 0, [], True
        self.changed = asyncio.Event()

    def set_state(self, state: str) -> None:
        if state == "speaking" and self.state != "speaking":
            self.spoke += 1
        self.state = state
        self.changed.set()

    async def settled(self, spoke_before: int) -> bool:
        """True once the agent has said something new and stayed listening for SETTLE seconds."""
        deadline = time.monotonic() + PATIENCE
        while self.connected and time.monotonic() < deadline:
            if self.spoke > spoke_before and self.state == "listening":
                quiet_since = time.monotonic()
                while self.state == "listening" and time.monotonic() - quiet_since < SETTLE:
                    await asyncio.sleep(0.1)
                if self.state == "listening":
                    return True
            await asyncio.sleep(0.1)
        return False


async def keep_line_open(source: rtc.AudioSource, speaking: asyncio.Event) -> None:
    """Send silence whenever no line is playing, as a SIP gateway sends a frame every 20 ms, words or not.
    Speech to text closes an utterance only once it hears silence after it. A caller that went quiet on
    the wire left its last words unheard whenever a recording ended right on them."""
    silence = rtc.AudioFrame(bytes(FRAME * 2), PHONE_RATE, 1, FRAME)
    while True:
        if not speaking.is_set() and source.queued_duration < 0.1:
            await source.capture_frame(silence)
        await asyncio.sleep(0.01)


async def say(source: rtc.AudioSource, path: Path, ear: Ear, speaking: asyncio.Event) -> bool:
    """Play one line in real time. Returns True if the agent started speaking over the caller."""
    with wave.open(str(path)) as recording:
        pcm = np.frombuffer(recording.readframes(recording.getnframes()), dtype=np.int16)
    speaking_at_start = ear.state == "speaking"
    speaking.set()
    # The whole line goes into the source's queue at once and LiveKit's native side paces it out, so a
    # busy Python loop cannot leave gaps that the far end's jitter buffer would throw away as late audio.
    for start in range(0, len(pcm), FRAME):
        chunk = np.pad(pcm[start:start + FRAME], (0, max(0, FRAME - len(pcm[start:start + FRAME]))))
        await source.capture_frame(rtc.AudioFrame(chunk.tobytes(), PHONE_RATE, 1, FRAME))
    playing, talked_over = asyncio.ensure_future(source.wait_for_playout()), False
    while not playing.done():
        talked_over = talked_over or (ear.state == "speaking" and not speaking_at_start)
        await asyncio.sleep(0.05)
    speaking.clear()
    return talked_over


async def place_call(script: dict, room_name: str) -> dict:
    """Ring in, talk through the script, and hang up. Returns what the caller observed."""
    ear, room = Ear(), rtc.Room()
    started, agent_sids = time.monotonic(), set()

    def watch_agent(participant: rtc.RemoteParticipant) -> None:
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            ear.set_state(participant.attributes.get("lk.agent.state", ear.state))

    @room.on("participant_attributes_changed")
    def on_attributes(_changed, participant) -> None:
        watch_agent(participant)

    @room.on("track_published")
    def on_track(publication, participant) -> None:
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            agent_sids.add(publication.sid)

    @room.on("disconnected")
    def on_disconnected(_reason) -> None:
        ear.connected = False
        ear.changed.set()

    async def read_sentence(reader: rtc.TextStreamReader) -> None:
        text = await reader.read_all()
        # The stream carries both sides' transcripts; only the agent's own track is what the caller heard.
        if reader.info.attributes.get("lk.transcribed_track_id") in agent_sids and text.strip():
            ear.sentences.append(text.strip())

    room.register_text_stream_handler(
        "lk.transcription", lambda reader, _identity: asyncio.ensure_future(read_sentence(reader)))
    await room.connect(livekit_settings()[0], caller_token(script, room_name))
    for participant in room.remote_participants.values():
        watch_agent(participant)
        agent_sids.update(publication.sid for publication in participant.track_publications.values()
                          if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)
    source = rtc.AudioSource(PHONE_RATE, 1, queue_size_ms=QUEUE_MS)
    track = rtc.LocalAudioTrack.create_audio_track("phone", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    speaking = asyncio.Event()
    line_open = asyncio.ensure_future(keep_line_open(source, speaking))

    lines, said, talked_over, spoke_before, heard_before = script["lines"], -1, 0, 0, 0
    while ear.connected:
        if not await ear.settled(spoke_before):
            break
        agent_words = " ".join(ear.sentences[heard_before:])
        heard_before, spoke_before = len(ear.sentences), ear.spoke
        line = next_line(lines, said, agent_words)
        if line is None:
            break
        said = line
        talked_over += await say(source, line_audio(script["id"], line + 1), ear, speaking)
    line_open.cancel()
    hung_up_by = "agent" if not ear.connected else "caller"
    if ear.connected:
        await room.disconnect()
    return {"lines_said": said + 1, "talked_over": talked_over, "hung_up_by": hung_up_by,
            "seconds": round(time.monotonic() - started, 1)}
