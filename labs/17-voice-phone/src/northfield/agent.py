"""The front desk: a LiveKit voice agent that answers Northfield's inbound line.

One phone call is one LiveKit room. The caller joins as a SIP participant, the agent joins as a worker
job, and the session runs the voice pipeline: voice activity detection cuts the caller's audio into
speech, the turn detector decides whether they have finished, LiveKit Inference writes it down, the model
picks a tool or a reply, and LiveKit Inference speaks it. The session is the shape of LiveKit's own
agent-starter-python. Every change lands in the booking system, never in the model."""

import json
import os
import time
from pathlib import Path
from typing import Literal

from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    function_tool,
    inference,
    room_io,
)
from livekit.agents.beta.tools import EndCallTool
from livekit.plugins import openai

from northfield.config import AGENT_VOICE, STT_MODEL, TTS_MODEL, cost_of, llm_settings
from northfield.schedule import Schedule, changes, original

# Callers hear this before the model says a word, so the AI and the recording are disclosed every time.
GREETING = ("Thanks for calling Northfield Heating and Air. This is the automated assistant, and this call "
            "is recorded. How can I help?")

INSTRUCTIONS = """Answer the phone for Northfield Heating & Air, a heating and cooling company in Columbus, Ohio.
Everything you say is spoken on a phone call: at most two short sentences and about 30 words per
turn, no lists, no markdown, no symbols. Say times and prices in words a person would say.

Today is Monday 19 October 2026. {hours}
Prices: a tune-up is {tune_up}. A repair visit is {repair}. An estimate for new equipment is {estimate}.

The caller: {caller}

What you can do:
- Book a tune-up, a repair or an estimate for new equipment. Call find_windows first and offer only
  open windows. Read back the kind and the window and get a yes before you call book_visit.
- Move or cancel one of the caller's booked visits. Caller ID can be faked, so first get the house number
  of the address on file and pass it to the tool. Never say the address yourself. Get a yes before you
  change it.
- A new caller needs a name, street address and zip before you book. If book_visit says the zip is
  outside the service area, say so kindly and do not transfer.
- Answer questions about prices and hours from the facts above, and nothing else.

When to hand the call to a person with transfer_to_human:
- safety: a gas smell, a carbon monoxide alarm, sparks or burning. First tell them to leave the house
  now and call the gas company or 911 from outside, then transfer.
- billing: charges, refunds and invoices.
- caller_asked: the caller asks for a person.
- other: anything you cannot do with your tools.
Never promise what a person will do; say who will pick up.

When the caller says goodbye or has nothing else, say a short goodbye and call end_call."""

TEAMS = {"safety": "the on-call dispatcher", "billing": "our billing team",
         "caller_asked": "someone in the office", "other": "someone in the office"}


class CallLog:
    """The call as the wallboard sees it: status.json while it runs, result.json when it ends."""

    def __init__(self, folder: Path, call_id: str, number: str):
        self.folder, self.started = folder, time.time()
        self.state = {"call": call_id, "number": number, "agent": "connecting", "turns": [], "tools": [],
                      "tokens": {"input": 0, "cached": 0, "output": 0}, "cost": 0.0, "ended": False}
        folder.mkdir(parents=True, exist_ok=True)
        self.write()

    def write(self) -> None:
        self.state["seconds"] = round(time.time() - self.started, 1)
        # Written whole and renamed, so the page never reads half a file.
        partial = self.folder / "status.json.part"
        partial.write_text(json.dumps(self.state, indent=1))
        partial.replace(self.folder / "status.json")

    def turn(self, who: str, text: str, metrics: dict) -> None:
        if not text.strip():
            return
        spoken_at = metrics.get("started_speaking_at") or time.time()
        entry = {"who": who, "text": text.strip(), "at": round(spoken_at - self.started, 1)}
        # The delays a caller feels: how long the turn detector waited, and how long until the agent spoke.
        for name in ("end_of_turn_delay", "transcription_delay", "e2e_latency", "llm_node_ttft", "tts_node_ttfb"):
            if metrics.get(name) is not None:
                entry[name] = round(metrics[name], 2)
        self.state["turns"].append(entry)
        self.write()

    def tool(self, name: str, arguments: dict, result: dict) -> None:
        self.state["tools"].append({"name": name, "arguments": arguments, "result": result})
        self.write()

    def usage(self, model: str, input_tokens: int, cached: int, output_tokens: int) -> None:
        self.state["tokens"] = {"input": input_tokens, "cached": cached, "output": output_tokens}
        self.state["cost"] = round(cost_of(model, input_tokens, cached, output_tokens), 6)
        self.write()


class FrontDesk(Agent):
    def __init__(self, book: Schedule, number: str, log: CallLog):
        facts = book.book_of_record
        super().__init__(
            instructions=INSTRUCTIONS.format(hours=facts["hours"], tune_up=facts["prices"]["tune-up"],
                                             repair=facts["prices"]["repair"], estimate=facts["prices"]["estimate"],
                                             caller=book.caller_card(number)),
            tools=[EndCallTool(delete_room=False, ignore_on_enter=True)],
        )
        self.book, self.number, self.log = book, number, log

    def record(self, name: str, arguments: dict, result: dict) -> str:
        # The model reads the result as JSON; the log keeps the same dict for the wallboard and the review.
        self.log.tool(name, arguments, result)
        return json.dumps(result)

    @function_tool
    async def find_windows(self, context: RunContext) -> str:
        """List this week's visit windows that still have a spot, with their ids."""
        windows = [{"id": slot["id"], "when": f"{slot['day']}, {slot['time']}"} for slot in self.book.open_windows()]
        return self.record("find_windows", {}, {"open": windows})

    @function_tool
    async def book_visit(self, context: RunContext, kind: Literal["tune-up", "repair", "estimate"], window_id: str,
                         problem: str, name: str = "", address: str = "", zip_code: str = "") -> str:
        """Book a visit for the caller after they said yes. Name, address and zip only for a new caller.

        Args:
            kind: tune-up, repair, or estimate for new equipment.
            window_id: An id from find_windows, such as THU-AM.
            problem: The caller's problem in a few words.
            name: A new caller's full name.
            address: A new caller's street address and city.
            zip_code: A new caller's five digit zip.
        """
        arguments = {"kind": kind, "window_id": window_id, "problem": problem, "zip_code": zip_code}
        return self.record("book_visit", arguments,
                           self.book.book(self.number, kind, window_id, problem, name, address, zip_code))

    @function_tool
    async def move_visit(self, context: RunContext, visit_id: str, window_id: str, house_number: str) -> str:
        """Move one of the caller's booked visits to another open window, after they said yes.

        Args:
            visit_id: The visit to move, such as V-2041.
            window_id: An id from find_windows.
            house_number: The house number the caller gave for the address on file.
        """
        return self.record("move_visit", {"visit_id": visit_id, "window_id": window_id},
                           self.book.move(self.number, visit_id, window_id, house_number))

    @function_tool
    async def cancel_visit(self, context: RunContext, visit_id: str, house_number: str) -> str:
        """Cancel one of the caller's booked visits, after they said yes.

        Args:
            visit_id: The visit to cancel, such as V-2043.
            house_number: The house number the caller gave for the address on file.
        """
        return self.record("cancel_visit", {"visit_id": visit_id}, self.book.cancel(self.number, visit_id, house_number))

    @function_tool
    async def transfer_to_human(self, context: RunContext, reason: Literal["safety", "billing", "caller_asked", "other"],
                                summary: str) -> str:
        """Hand the call to a person in the office. The summary is what they read before they pick up.

        Args:
            reason: safety, billing, caller_asked, or other.
            summary: One sentence: who is calling and what they need.
        """
        result = self.record("transfer_to_human", {"reason": reason, "summary": summary},
                             self.book.hand_off(self.number, reason, summary))
        if json.loads(result)["ok"]:
            # Once the hand-over sentence has played, the agent leaves and the person has the line.
            context.speech_handle.add_done_callback(lambda _: context.session.shutdown())
            return f"Say in one short sentence that you are connecting them to {TEAMS[reason]} now. Nothing else."
        return result


def turn_handling() -> dict:
    """LiveKit's turn detector, as in the starter; TURN_DETECTION=vad makes silence alone end a turn."""
    detector = "vad" if os.environ.get("TURN_DETECTION") == "vad" else inference.TurnDetector()
    # The model listens to the caller's audio, so "and, um..." keeps the turn open where silence alone would
    # close it. Preemptive generation starts the reply while the detector is still deciding.
    # Endpointing bounds that decision. Below 0.6 s the phone-band transcript lands after the turn closed,
    # so the agent answered half a sentence; past 1.5 s an unsure detector is only dead air on the line.
    return {"turn_detection": detector, "preemptive_generation": {"enabled": True},
            "endpointing": {"min_delay": 0.6, "max_delay": 1.5}}


def build_session(llm_model, stt_model, tts_model, vad_model) -> AgentSession:
    return AgentSession(stt=stt_model, vad=vad_model, llm=llm_model, tts=tts_model, turn_handling=turn_handling())


def live_session() -> tuple[AgentSession, str]:
    base_url, api_key, model = llm_settings()
    # A caller hears every second the model spends reasoning as dead air, so reasoning is kept minimal.
    # OpenRouter's default routing is kept: its "sort by latency" option measured a 5 s first word on one
    # request in eight, where default routing never passed 0.8 s.
    llm_model = openai.LLM(model=model, base_url=base_url, api_key=api_key, reasoning_effort="minimal")
    # Speech runs on LiveKit Inference, streamed both ways, so the first words of a reply play while the
    # rest is still being written. Nothing loads on this laptop.
    stt_model = inference.STT(model=STT_MODEL, language="en")
    tts_model = inference.TTS(model=TTS_MODEL, voice=AGENT_VOICE)
    return build_session(llm_model, stt_model, tts_model, inference.VAD()), model


def watch(session: AgentSession, log: CallLog, model: str) -> None:
    """Copy what the session does into the call log as it happens."""

    @session.on("conversation_item_added")
    def on_item(event) -> None:
        role = getattr(event.item, "role", "")
        if role in ("user", "assistant"):
            log.turn("caller" if role == "user" else "agent", event.item.text_content or "", dict(event.item.metrics or {}))

    @session.on("agent_state_changed")
    def on_state(event) -> None:
        log.state["agent"] = event.new_state
        log.write()

    @session.on("session_usage_updated")
    def on_usage(event) -> None:
        for usage in event.usage.model_usage:
            if getattr(usage, "type", "") == "llm_usage":
                log.usage(model, usage.input_tokens, usage.input_cached_tokens, usage.output_tokens)


async def answer(ctx: JobContext) -> None:
    """One inbound call, from the caller joining to the room closing."""
    plan = json.loads(ctx.job.metadata or "{}")
    folder = Path(plan.get("folder", f"out/calls/{ctx.room.name}"))
    await ctx.connect()
    caller = await ctx.wait_for_participant(kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP)
    # A SIP trunk puts the caller's number in this attribute; caller ID is how the agent knows who rang.
    number = caller.attributes.get("sip.phoneNumber", "")
    book, log = Schedule(folder / "schedule.json"), CallLog(folder, plan.get("call", ctx.room.name), number)
    session, model = live_session()
    watch(session, log, model)

    async def finish() -> None:
        log.state.update(ended=True, agent="ended", changes=changes(original(), book.book_of_record))
        log.write()
        (folder / "result.json").write_text(json.dumps(log.state, indent=1))

    ctx.add_shutdown_callback(finish)

    @session.on("close")
    def on_close(_) -> None:
        ctx.shutdown(reason="call ended")

    # Deleting the room on close hangs up the other side, as a carrier drops the line when one party leaves.
    # Transcripts go out whole, not word-synced to the audio: the phone caller never sees them anyway.
    options = room_io.RoomOptions(delete_room_on_close=True, text_output=room_io.TextOutputOptions(sync_transcription=False))
    await session.start(agent=FrontDesk(book, number, log), room=ctx.room, room_options=options, record=False)
    await session.say(GREETING, allow_interruptions=False, add_to_chat_ctx=True)


def run_worker() -> None:
    """Register with LiveKit and answer every call dispatched to this agent's name, until stopped."""
    import asyncio
    import logging

    from livekit.agents import AgentServer, JobExecutorType

    from northfield.config import AGENT_NAME, livekit_settings

    url, key, secret = livekit_settings()
    # out/agent.log then shows each job the worker takes or turns down, the first place to look when a call rings out.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Explicit dispatch: the worker only takes calls sent to this name, as a phone number's dispatch rule does.
    os.environ.setdefault("LIVEKIT_AGENT_NAME", AGENT_NAME)
    # Calls run one at a time in this lab, so a thread per call is enough and starts at once.
    server = AgentServer(job_executor_type=JobExecutorType.THREAD, ws_url=url, api_key=key,
                         api_secret=secret, port=8817, num_idle_processes=2)
    server.rtc_session(answer)
    server.on("worker_registered", lambda *_: print("agent registered and waiting for calls", flush=True))
    asyncio.run(server.run(devmode=True))
