"""Speech on this machine, as LiveKit plugins: faster-whisper hears the caller, Kokoro speaks the reply.

LiveKit Agents takes any STT and TTS that implement its two small interfaces, so swapping these for a
hosted Deepgram or Cartesia is one line in agent.py. Local models keep the lab free of speech keys."""

import asyncio
import uuid
from functools import cache

import numpy as np
from livekit.agents import APIConnectOptions, stt, tts, utils

from northfield.config import KOKORO_DIR, WHISPER_MODEL
from northfield.phone_audio import resample

# Whisper reads the prompt as the text that came before, so a sentence the office says steers its spelling
# of the company's name. A list of words here leaks: Whisper once wrote "thermostat" the caller never said.
VOCABULARY = "Thanks for calling Northfield Heating and Air in Columbus."


@cache
def whisper():
    from faster_whisper import WhisperModel

    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")


@cache
def kokoro():
    from kokoro_onnx import Kokoro

    return Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))


def transcribe(samples: np.ndarray) -> str:
    """16 kHz float samples to text, greedy and with the office's words as the prompt."""
    segments, _ = whisper().transcribe(samples, language="en", beam_size=1, initial_prompt=VOCABULARY,
                                       vad_filter=False, condition_on_previous_text=False)
    return " ".join(segment.text.strip() for segment in segments).strip()


def speak(text: str, voice: str) -> tuple[np.ndarray, int]:
    samples, rate = kokoro().create(text, voice=voice, speed=1.0, lang="en-us")
    return samples, rate


class WhisperSTT(stt.STT):
    """Not streaming: the session's voice activity detector cuts each utterance and hands it over whole."""

    def __init__(self):
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))

    @property
    def model(self) -> str:
        return WHISPER_MODEL

    async def _recognize_impl(self, buffer, *, language=None, conn_options: APIConnectOptions) -> stt.SpeechEvent:
        frame = utils.merge_frames(buffer)
        pcm = np.frombuffer(frame.data, dtype=np.int16).reshape(-1, frame.num_channels)[:, 0]
        samples = resample(pcm.astype(np.float32) / 32768, frame.sample_rate, 16000)
        text = await asyncio.to_thread(transcribe, samples)
        return stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT, request_id=uuid.uuid4().hex,
                               alternatives=[stt.SpeechData(language="en", text=text)])


class KokoroTTS(tts.TTS):
    def __init__(self, voice: str):
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False), sample_rate=24000, num_channels=1)
        self.voice = voice

    @property
    def model(self) -> str:
        return "kokoro-v1.0"

    def synthesize(self, text: str, *, conn_options: APIConnectOptions = APIConnectOptions()) -> "KokoroStream":
        return KokoroStream(tts=self, input_text=text, conn_options=conn_options)


class KokoroStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        samples, rate = await asyncio.to_thread(speak, self._input_text, self._tts.voice)
        output_emitter.initialize(request_id=uuid.uuid4().hex, sample_rate=rate, num_channels=1, mime_type="audio/pcm")
        output_emitter.push((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
        output_emitter.flush()
