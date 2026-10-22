"""Lucca Brick Oven's phone line as one Pipecat bot, in the shape of Pipecat's own voice examples.

    caller audio -> speech to text -> user turn -> model + till tools -> text to speech -> caller's ear

Every stage runs on this Mac except the model: Whisper on the GPU through MLX, Silero and Smart Turn to
decide when the caller has finished, Kokoro for the voice. Only the transcript goes to the model.

The same `run_bot` answers two kinds of call. `pipecat eval suite` starts this file with `-t eval` and
plays a recorded caller into it; `phoneline serve` hands it a browser's WebRTC connection. When the call
ends, the bot writes what the till holds and how long each turn took to out/calls/<call>.json.
"""

import asyncio
import json
import os
import platform
import sys
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.evals.transport import EvalTransportParams
from pipecat.frames.frames import ErrorFrame, TranscriptionFrame, TTSSpeakFrame
from pipecat.observers.user_bot_latency_observer import MeasuredFrom, UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.tts_service import TTSService
from pipecat.services.whisper.stt import WhisperSTTServiceMLX
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.utils.time import time_now_iso8601
from pipecat.workers.runner import WorkerRunner

from lucca.agent import GREETING, MeteredLLM, OrderTools
from lucca.config import AGENT_VOICE, llm_settings, stt_model
from lucca.till import Menu, Till

LAB = Path(__file__).resolve().parents[2]
CALLS_OUT = Path("out/calls")
LIVE_ORDER_NUMBER = 500  # live calls take ticket numbers apart from the recorded shift

# The eval suite starts a bot in its own log folder, so the bot finds data/ and out/ from the lab root.
os.chdir(LAB)

transport_params = {
    "eval": lambda: EvalTransportParams(audio_in_enabled=True, audio_out_enabled=True),
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}

# Pipecat names every part of a turn's wait. A caller feels three: being heard, the model deciding, and
# the voice starting. Anything Pipecat adds later lands in speak, the end nearest the caller's ear.
HEAR = {"endpointing_wait", "transcription", "turn_detection", "turn_completion", "waiting_for_user"}
THINK = {"first_request", "llm_inference", "llm_tool_call", "function_handler", "awaiting_speakable_text"}


def split_turn(breakdown) -> dict:
    """One turn's latency in milliseconds: the whole gap and its three parts."""
    parts = {"hear_ms": 0.0, "think_ms": 0.0, "speak_ms": 0.0}
    for part in breakdown.contributions:
        name = "hear_ms" if part.key in HEAR else "think_ms" if part.key in THINK else "speak_ms"
        parts[name] += part.duration_secs * 1000
    return {"latency_ms": round(breakdown.total_secs * 1000), **{name: round(ms) for name, ms in parts.items()},
            "parts": [{"key": part.key, "ms": round(part.duration_secs * 1000)} for part in breakdown.contributions]}


def on_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


class MenuHearing(WhisperSTTServiceMLX):
    """Pipecat's MLX Whisper, primed with the menu. Whisper reads its prompt as words already said, so a
    caller's "two cannolis" comes out as the menu spells it instead of "two canoles", which the till
    cannot match. Pipecat's MLX service does not pass a prompt to Whisper, so this one does."""

    def __init__(self, hint: str, **kwargs):
        super().__init__(**kwargs)
        self.hint = hint

    async def run_stt(self, audio: bytes):
        import mlx_whisper

        await self.start_processing_metrics()
        # Pipecat hands over 16 bit PCM; Whisper wants floats between -1 and 1.
        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        try:
            heard = await asyncio.to_thread(
                mlx_whisper.transcribe, samples, path_or_hf_repo=self._settings.model,
                temperature=self._settings.temperature, language=self._settings.language,
                initial_prompt=self.hint)
        except Exception as error:  # a failed utterance is reported, and the call goes on
            yield ErrorFrame(error=f"Speech to text failed: {error}")
            return
        await self.stop_processing_metrics()
        # As Pipecat does: drop segments Whisper itself thinks are probably not speech.
        text = " ".join(segment["text"].strip() for segment in heard.get("segments", [])
                        if segment.get("no_speech_prob", 0.0) < self._settings.no_speech_prob).strip()
        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), self._settings.language)


def hearing() -> MenuHearing:
    # Whisper hears a whole utterance once the voice detector says it ended, so its time is part of every
    # turn. On Apple Silicon MLX runs it on the GPU: base.en hears a five second order in about 0.2 s.
    return MenuHearing(Menu().hint(), settings=WhisperSTTServiceMLX.Settings(model=stt_model()))


def speaking() -> TTSService:
    # Kokoro speaks sentence by sentence as the model streams, so the caller hears the first sentence
    # while the model is still writing the second. On Apple Silicon it runs on the GPU, twice as fast.
    if on_apple_silicon():
        from lucca.voice import GPUVoice

        return GPUVoice(voice=AGENT_VOICE)
    return KokoroTTSService(settings=KokoroTTSService.Settings(voice=AGENT_VOICE))


def thinking() -> MeteredLLM:
    base_url, key, model = llm_settings()
    return MeteredLLM(model=model, api_key=key, base_url=base_url)


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments,
                  on_event: Callable[[dict], None] = lambda event: None) -> dict:
    body = runner_args.body or {}
    call_id = body.get("call", "live")
    started, events, turns = time.monotonic(), [], []

    def record(event: dict) -> None:
        stamped = {"at_ms": round((time.monotonic() - started) * 1000), **event}
        events.append(stamped)
        on_event(stamped)

    till = Till()
    tools = OrderTools(till, body.get("order_number", LIVE_ORDER_NUMBER), record)
    llm = thinking()
    context = LLMContext(tools=tools.schema())
    # Pipecat's default turn taking: Silero hears speech start and stop, and the Smart Turn model decides
    # whether a pause means the caller has finished or is still thinking of the next item.
    user, assistant = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()))

    pipeline = Pipeline([transport.input(), hearing(), user, llm, speaking(), transport.output(), assistant])
    latency = UserBotLatencyObserver()
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[latency],
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    measured = []

    @latency.event_handler("on_latency_measured")
    async def on_latency_measured(observer, seconds):
        # Fires only for replies to the caller, timed from the moment they went quiet: the wait the
        # caller felt. Every reply counts; the greeting does not.
        measured.append(seconds)

    @latency.event_handler("on_latency_breakdown")
    async def on_latency_breakdown(observer, breakdown):
        if not measured:
            return
        turn = {"latency_ms": round(measured.pop() * 1000), "hear_ms": None, "think_ms": None,
                "speak_ms": None, "parts": []}
        # A caller who pauses mid-sentence makes the bot start a reply it then drops. Pipecat discards
        # that dropped cycle's parts, so such a turn keeps its total and no split.
        if breakdown.measured_from is MeasuredFrom.USER_SILENCE:
            turn.update({key: value for key, value in split_turn(breakdown).items() if key != "latency_ms"})
        turns.append(turn)
        record({"kind": "turn", **turn})

    @user.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message):
        record({"kind": "caller", "text": message.content})

    @assistant.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(aggregator, message):
        record({"kind": "agent", "text": message.content, "interrupted": message.interrupted})

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        # The greeting is spoken, not generated: it is the same on every call, so it costs no model call.
        await worker.queue_frames([TTSSpeakFrame(GREETING, append_to_context=True)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await runner.cancel()

    await runner.run()
    result = {"id": call_id, "ticket": till.ticket(), "turns": turns, "cost": llm.cost(),
              "llm_calls": len(llm.usage), "events": events}
    CALLS_OUT.mkdir(parents=True, exist_ok=True)
    (CALLS_OUT / f"{call_id}.json").write_text(json.dumps(result, indent=1))
    logger.info(f"{call_id}: {len(turns)} turns timed, ticket {till.ticket()['status']}")
    return result


async def bot(runner_args: RunnerArguments):
    """Main bot entry point, as Pipecat's runner and eval suite call it."""
    if on_apple_silicon():
        from lucca.voice import warm

        # The harness and the browser wait for the line to open; a caller never waits for the voice to load.
        await warm(AGENT_VOICE)
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


def runner_main() -> Callable[[], None]:
    """Pipecat's runner entry point. Importing it loads the key file over the shell's settings, so the
    shell's are put back: a key or model set for one run wins here too, as everywhere else in the lab."""
    shell = dict(os.environ)
    from pipecat.runner.run import main

    os.environ.update(shell)
    return main


if __name__ == "__main__":
    runner_main()()
