"""The voice pipeline: audio in, text, a model with till tools, speech out, as one chain of Pipecat frames.

Every stage is a processor that receives frames and pushes new ones downstream:

    caller audio -> speech to text -> user turn -> model + tools -> text to speech -> caller's ear

Speech to text (Whisper) and text to speech (Kokoro) run on this machine, so caller audio never leaves
it; only the transcript goes to the model. The same chain serves a recorded call and a live browser call;
only the transport at each end changes.
"""

import asyncio
import time
from collections.abc import Callable

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import EndFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from lucca.agent import GREETING, MeteredLLM, OrderTools
from lucca.caller import RecordedLine, play_call
from lucca.config import AGENT_VOICE, llm_settings, stt_model
from lucca.till import Menu, Till
from lucca.turns import TurnClock

SENTENCE_PAUSE = 0.8  # seconds of quiet that end a caller's sentence
TRANSCRIPT_WAIT = 60.0  # seconds a turn may wait for Whisper before Pipecat gives up and ends it empty
# Whisper guesses at words it rarely hears. Biasing it toward the menu turns "can only" into "cannoli".
MENU_WORDS = "Lucca pepperoni margherita cannoli Caesar garlic knots parmesan buffalo wings gluten free Sprite"


def hearing(model: str | None = None) -> WhisperSTTService:
    """Speech to text. Whisper transcribes a whole utterance once the voice detector says it ended, so its
    cost lands in the hear part of every turn: a bigger model hears better and makes every turn slower."""
    settings = WhisperSTTService.Settings(model=model or stt_model(), hotwords=MENU_WORDS)
    return WhisperSTTService(settings=settings, device="cpu", compute_type="int8")


def speaking() -> KokoroTTSService:
    # Kokoro speaks sentence by sentence as the model streams, so the caller hears the first sentence
    # while the model is still writing the second.
    # On a busy CPU the first sentence can take many seconds to synthesise. Pipecat gives up on a
    # sentence after 3 s of silence by default, so the wait is raised to keep slow audio, not drop it.
    return KokoroTTSService(settings=KokoroTTSService.Settings(voice=AGENT_VOICE), stop_frame_timeout_s=120)


def thinking() -> MeteredLLM:
    base_url, key, model = llm_settings()
    return MeteredLLM(model=model, api_key=key, base_url=base_url)


class OrderLine:
    """One call's pipeline, with the till it fills and the clock that times it."""

    def __init__(self, transport, till: Till, order_number: int, llm: MeteredLLM,
                 on_event: Callable[[dict], None] = lambda event: None, stt_name: str | None = None):
        self.till, self.llm, self.started = till, llm, time.monotonic()
        self.events: list[dict] = []
        self.listener = on_event
        self.tools = OrderTools(till, order_number, self.event)
        context = LLMContext(tools=self.tools.schema())
        # The user aggregator owns turn-taking: Silero hears speech start, which interrupts the agent if
        # it is talking, and the Smart Turn model decides when a pause means the caller has finished.
        #
        # Whisper hears a whole segment at once, so a segment must be a whole sentence: the detector waits
        # for 0.8 s of quiet, not Pipecat's 0.2 s, or "a large pepperoni, and a dozen wings" arrives as two
        # turns and the model answers half an order. A turn starts on voice alone; a late transcript
        # from a slow CPU must not open a second turn that interrupts the reply.
        #
        # Pipecat's watchdog ends a turn after 5 s with no stop. On a busy CPU Whisper can take longer,
        # and the watchdog then ends the turn before the transcript lands, so the model never runs.
        detector = SileroVADAnalyzer(params=VADParams(stop_secs=SENTENCE_PAUSE))
        strategies = UserTurnStrategies(start=[VADUserTurnStartStrategy()])
        turns = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(
            vad_analyzer=detector, user_turn_strategies=strategies, user_turn_stop_timeout=TRANSCRIPT_WAIT))
        self.transport = transport
        self.pipeline = Pipeline([transport.input(), hearing(stt_name), turns.user(), llm, speaking(),
                                  transport.output(), turns.assistant()])
        self.clock = TurnClock(llm, transport.output(), self.event)
        self.worker = PipelineWorker(self.pipeline, observers=[self.clock], idle_timeout_secs=None,
                                     params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=24000))

    def event(self, event: dict) -> None:
        stamped = {"at_ms": round((time.monotonic() - self.started) * 1000), **event}
        self.events.append(stamped)
        self.listener(stamped)

    async def greet(self) -> None:
        # The greeting is spoken, not generated: it is the same on every call, so it costs no model call.
        await self.worker.queue_frames([TTSSpeakFrame(GREETING, append_to_context=True)])

    async def hang_up(self) -> None:
        await self.worker.queue_frame(EndFrame())

    def report(self) -> dict:
        return {"ticket": self.till.ticket(), "turns": self.clock.summary(), "cost": self.llm.cost(),
                "llm_calls": len(self.llm.usage), "events": self.events}


async def run_call(call: dict, order_number: int, on_event: Callable[[dict], None] = lambda event: None,
                   stt_name: str | None = None, menu: Menu | None = None) -> dict:
    """Play one recorded call through the whole pipeline and return the ticket, the turn timings and
    the cost."""
    line = RecordedLine()
    order = OrderLine(line, Till(menu=menu or Menu()), order_number, thinking(), on_event, stt_name)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(order.worker)
    running = asyncio.create_task(runner.run())
    await order.greet()
    finished = await play_call(call, line, order.clock, order.event)
    await order.hang_up()
    await running
    return {"id": call["id"], "caller": call["caller"], "finished": finished, **order.report()}
