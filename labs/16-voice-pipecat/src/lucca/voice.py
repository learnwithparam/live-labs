"""The ordering line's voice: Kokoro on the Mac's GPU through MLX, adapted from Pipecat's macOS local
voice agent (github.com/kwindla/macos-local-voice-agents).

The caller hears nothing until the first sentence is synthesized, so the voice sits inside every turn's
wait. Kokoro on the CPU took 1.7 s to start a 17 word sentence on an M1; on the GPU it takes 0.8 s, and
the CPU stays free for hearing the caller.

MLX's GPU work must not share threads with Pipecat's event loop, so the model lives in its own process.
That process reads one sentence per line and writes each spoken segment back as soon as it exists.
"""

import asyncio
import base64
import json
import sys
from collections.abc import AsyncGenerator

import numpy as np
from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import ErrorFrame, Frame, StartFrame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

MODEL = "mlx-community/Kokoro-82M-bf16"
KOKORO_RATE = 24000  # Kokoro speaks at 24 kHz; the service resamples to whatever the transport plays
# One segment is a whole clause of audio, a few hundred kilobytes once encoded, and arrives as one line.
# asyncio reads lines of at most 64 KB by default, so the pipe's limit is raised well past a segment.
LINE_LIMIT = 16 * 1024 * 1024


class Worker:
    """One Kokoro process on the GPU. It answers sentences in order, one at a time."""

    def __init__(self, process: asyncio.subprocess.Process):
        self.process, self.sentence = process, 0
        # Two sentences in flight would interleave their audio, so each waits for the one before.
        self.lock = asyncio.Lock()

    async def say(self, text: str):
        """Yield 16 bit 24 kHz audio for one sentence, a segment at a time."""
        async with self.lock:
            self.sentence += 1
            self.process.stdin.write(json.dumps({"sentence": self.sentence, "text": text}).encode() + b"\n")
            await self.process.stdin.drain()
            while True:
                reply = json.loads(await self.process.stdout.readline() or b'{"error": "the voice stopped"}')
                # A caller who interrupts cancels a sentence mid-way. Its leftover segments still arrive,
                # tagged with the old sentence number, and are dropped here.
                if reply.get("sentence", self.sentence) != self.sentence:
                    continue
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                if reply.get("done"):
                    return
                yield base64.b64decode(reply["audio"])


workers: dict[str, Worker] = {}


async def warm(voice: str) -> Worker:
    """Start the voice once per process and wait until it can speak. Loading takes several seconds, so
    the bot calls this before it answers the line, never inside a caller's turn."""
    if voice not in workers or workers[voice].process.returncode is not None:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "lucca.voice", voice,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=LINE_LIMIT)
        ready = json.loads(await process.stdout.readline() or b'{"error": "the voice process exited"}')
        if not ready.get("ready"):
            raise RuntimeError(f"The voice did not start: {ready.get('error')}")
        workers[voice] = Worker(process)
    return workers[voice]


class GPUVoice(TTSService):
    """A Pipecat text to speech service whose engine is the Kokoro worker on the GPU."""

    def __init__(self, *, voice: str, **kwargs):
        super().__init__(push_start_frame=True, push_stop_frames=True,
                         settings=TTSSettings(model=MODEL, voice=voice, language=None), **kwargs)
        self.voice, self.worker = voice, None
        self.resampler = create_stream_resampler()

    def can_generate_metrics(self) -> bool:
        return True

    async def start(self, frame: StartFrame):
        await super().start(frame)
        self.worker = await warm(self.voice)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        await self.start_tts_usage_metrics(text)
        try:
            async for pcm in self.worker.say(text):
                await self.stop_ttfb_metrics()
                audio = await self.resampler.resample(pcm, KOKORO_RATE, self.sample_rate)
                yield TTSAudioRawFrame(audio=audio, sample_rate=self.sample_rate, num_channels=1, context_id=context_id)
        except RuntimeError as error:
            yield ErrorFrame(error=f"The voice failed: {error}")
        finally:
            await self.stop_ttfb_metrics()


def speak_forever(voice: str) -> None:
    """The worker: load Kokoro on the GPU, then turn each sentence on stdin into 16 bit audio on stdout."""
    # stdout carries only the service's messages; anything the libraries print goes to stderr instead.
    channel, sys.stdout = sys.stdout, sys.stderr
    from mlx_audio.tts.utils import load_model

    def send(message: dict) -> None:
        channel.write(json.dumps(message) + "\n")
        channel.flush()

    try:
        model = load_model(MODEL)
        list(model.generate(text="Ready.", voice=voice))  # the first sentence compiles the GPU kernels
    except Exception as error:  # reported to the service, which fails the call with this message
        send({"error": str(error)})
        return
    send({"ready": True})
    for line in sys.stdin:
        request = json.loads(line)
        try:
            for segment in model.generate(text=request["text"], voice=voice):
                pcm = (np.clip(np.array(segment.audio), -1, 1) * 32767).astype(np.int16).tobytes()
                send({"sentence": request["sentence"], "audio": base64.b64encode(pcm).decode()})
            send({"sentence": request["sentence"], "done": True})
        except Exception as error:  # one bad sentence ends that sentence, not the call
            send({"sentence": request["sentence"], "error": str(error)})


if __name__ == "__main__":
    speak_forever(sys.argv[1])
