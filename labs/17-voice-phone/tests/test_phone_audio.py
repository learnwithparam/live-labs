"""The callers' audio is what a US phone line delivers: 8 kHz, telephone band only, mu-law noise."""

import asyncio
import wave

import numpy as np
from livekit import rtc
from livekit.agents import inference

from northfield.caller import line_audio, scripts
from northfield.phone_audio import PHONE_RATE, band_limit, phone_line


def tone(frequency: float, rate: int = 24000, seconds: float = 1.0) -> np.ndarray:
    return np.sin(2 * np.pi * frequency * np.arange(int(rate * seconds)) / rate).astype(np.float32)


def test_the_line_cuts_frequencies_a_phone_cannot_carry():
    assert np.abs(band_limit(tone(100), 24000)).max() < 0.01
    assert np.abs(band_limit(tone(6000), 24000)).max() < 0.01
    assert np.abs(band_limit(tone(1000), 24000)).max() > 0.9


def test_a_second_of_audio_becomes_8000_sixteen_bit_samples():
    narrow = phone_line(tone(1000), 24000)
    assert narrow.dtype == np.int16 and abs(len(narrow) - PHONE_RATE) <= 1


def test_every_scripted_line_is_recorded_as_phone_audio():
    for call_id, script in scripts().items():
        for number in range(1, len(script["lines"]) + 1):
            with wave.open(str(line_audio(call_id, number))) as recording:
                assert recording.getframerate() == PHONE_RATE and recording.getsampwidth() == 2


async def speech_probability(path) -> float:
    """The agent's own voice activity detector over one recording: its median score on the loud windows."""
    with wave.open(str(path)) as recording:
        samples = np.frombuffer(recording.readframes(recording.getnframes()), dtype=np.int16)
    stream, frame_size = inference.VAD().stream(), PHONE_RATE // 50
    for start in range(0, len(samples) - frame_size, frame_size):
        stream.push_frame(rtc.AudioFrame(samples[start:start + frame_size].tobytes(), PHONE_RATE, 1, frame_size))
    stream.end_input()
    scores = []
    async for event in stream:
        window = samples[max(0, int(event.timestamp * PHONE_RATE) - PHONE_RATE // 10):int(event.timestamp * PHONE_RATE)]
        if event.type == "inference_done" and len(window) and np.sqrt(np.mean(window.astype(np.float32) ** 2)) > 500:
            scores.append(event.probability)
    return float(np.median(scores))


def test_every_caller_sounds_like_speech_to_the_agent():
    # A whispered voice once recorded one caller. The detector scored it 0.16, so the agent missed whole
    # lines and the caller sat in silence. Every other recording scores 0.7 or more.
    for call_id, script in scripts().items():
        for number in range(1, len(script["lines"]) + 1):
            score = asyncio.run(speech_probability(line_audio(call_id, number)))
            assert score >= 0.6, f"{call_id} line {number} scores {score:.2f} as speech"
