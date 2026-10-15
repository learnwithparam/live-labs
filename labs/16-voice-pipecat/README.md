# Build a Voice AI Agent with Pipecat

Build a phone line that takes a pizzeria's orders by voice and keys them into the till. Speech to text
and the voice run on your machine; only the transcript goes to the model.

## The product job

Lucca Brick Oven is a pizzeria on Murray Avenue in Pittsburgh. On a Friday night the phone rings while
every hand is on the oven. SoundHound sells this job to restaurant chains: a voice agent answers, takes
the order and puts it in the point of sale, so staff only cook.

- **The source of truth** is the order a person keyed by hand from the same call. Each of the eight
  recorded calls in `data/calls.json` carries that keyed order, or the reason it must go to staff.
- **The metric** is orders taken with no staff, counted only on an exact match with the keyed order. The
  second number is turn latency at p50 and p95: the gap from the caller's last word to the first sound
  of the reply.

## What you will be able to do

Before: you can build a chat agent with tools, but a phone call is audio, it has no "send" button, and
callers talk over you. After this lab you can:

- build a Pipecat pipeline that turns audio into frames, frames into text, and text back into speech
- run speech to text (Whisper) and text to speech (Kokoro) locally, with no audio leaving the machine
- give the voice agent till tools, so the till decides what can be ordered
- let the caller interrupt the agent, and measure how fast it stops talking
- split each turn's latency into hear, think and speak, and read it at p50 and p95
- send an order the menu cannot hold to staff for a call back, with the reason

## The problem

A chat agent waits for the user to press send. A phone caller never does. Something has to decide that
a pause means "I'm done", and a pause after "a large pepperoni" is not the end of the order. Decide too
early and the agent answers half an order. Decide too late and the caller says "hello?".

Every turn then pays three waits in a row. The audio becomes text, the model thinks and calls tools,
and the text becomes audio. A chat user forgives two seconds. A caller hears two seconds of silence and
starts talking again, right over the reply.

And the order must be right. A voice agent that guesses keys a pizza nobody asked for, and the kitchen
bakes it.

## What you will build

Lucca Brick Oven's kitchen ticket display. A ticket fills in line by line as the caller speaks. Beside
it, the call transcript shows what the caller said, what Whisper heard, every till tool the model
called, and where the caller cut in. Under it, a latency strip shows each turn split into hear, think
and speak.

The top bar counts the shift: orders taken with no staff, calls handed to staff, and p50 and p95 turn
latency. A call the menu cannot hold shows a **Call back** card on its ticket, with the reason and what
staff decide. You can replay a recorded call, or call the line with your own microphone.

<img src="docs/kitchen-display.jpg" alt="Lucca Brick Oven kitchen display: a staff call back ticket for a half and half pizza, the call transcript with till tool calls, and the per-turn latency strip" width="720">

## Words you will use

- **Frame**: one piece of data moving through a Pipecat pipeline, such as 20 ms of audio, a transcript,
  a word of the reply, or "the caller started talking".
- **Pipeline**: the chain of processors a frame passes through: transport in, speech to text, the
  model, text to speech, transport out.
- **Transport**: where audio enters and leaves. Here it is either recorded calls or a WebRTC call from
  the browser.
- **VAD (voice activity detection)**: a small model, Silero, that hears when someone is speaking.
- **Turn detection**: deciding a pause means the caller has finished. Pipecat's Smart Turn model reads
  the audio for that.
- **Barge-in**: the caller talks over the agent. The agent should stop at once and listen.
- **Turn latency**: from the caller's last word to the first sound of the reply.
- **Human in the loop**: staff, who call back when the menu cannot hold the order.

## Set up

You need [uv](https://docs.astral.sh/uv/) and an [OpenRouter](https://openrouter.ai) key. The first run
downloads the Whisper and Kokoro models, about 800 MB.

```bash
uv sync
```

Expect: /Installed|Audited|Resolved/

Copy `env.example` to `.env` and add your key.

## 1. The till decides

`src/lucca/till.py` holds the menu and the ticket. The model never writes a ticket line itself. It
passes the caller's words, like "a dozen buffalo wings", and the till resolves them or refuses with the
next step.

```bash
uv run phoneline menu
```

Expect: /Wings: 6 piece \$\d+\.\d\d, 12 piece/

A pizza without a size, or wings without a sauce, is refused with "Ask the caller". Anything the menu
cannot hold, like a half and half pizza, is refused with "Call send_to_staff".

## 2. Recorded callers

The proof needs callers who say the same words the same way every run. `src/lucca/voices.py` spoke
each line of `data/calls.json` once, with a different Kokoro voice per caller. The wav files are
committed in `data/calls/`. Recording again skips every line that already has a file:

```bash
uv run phoneline voices
```

Expect: recorded 0 new lines

`src/lucca/caller.py` plays them as a phone line would: 20 ms chunks in real time, then silence. The
silence matters. The voice detector only knows a caller stopped because it keeps hearing quiet.

## 3. The pipeline

Read `src/lucca/pipeline.py`. One list is the whole agent:

1. `transport.input()` sends audio frames.
2. `hearing()` is Whisper. It transcribes once the voice detector says the sentence ended.
3. `turns.user()` decides the turn is over and adds the transcript to the context.
4. The model streams its reply and calls till tools from `src/lucca/agent.py`.
5. `speaking()` is Kokoro. It speaks each sentence as soon as the model finishes it.
6. `transport.output()` plays the audio, and `turns.assistant()` saves what was said.

The voice detector waits for 0.8 s of quiet before it ends a sentence, not Pipecat's 0.2 s. Whisper
hears a whole sentence at once, so "a large pepperoni, and a dozen wings" must reach it as one.

Play one call through it:

```bash
uv run phoneline call LB-01
```

Expect: /"status": "placed"/

Each caller line prints with its latency split into hear, think and speak, then the ticket.

## 4. Interruptions

In LB-03, Tom cuts in during the read-back: "Sorry, wait. Make the wings garlic parmesan instead." The
voice detector hears him, and Pipecat sends an `InterruptionFrame` through the pipeline. Text to speech
drops what it has queued, and the reply stops mid-word.

`src/lucca/turns.py` records how long that took, from Tom's first word to the stop. The agent then
calls `remove_item` and `add_item`, and reads the order back again. You will see it on the page in
step 6.

## 5. The latency budget

`TurnClock` in `src/lucca/turns.py` is a Pipecat observer. It sees every frame and stamps four moments
per turn: the caller's last word, the transcript, the model's first word and the first audio out.

- **hear** is Whisper plus turn detection.
- **think** is the model, including any tool calls before it speaks.
- **speak** is Kokoro making the first sentence.

On a quiet Apple Silicon laptop, aim for under 1.5 s in total. Hear is the part a bigger Whisper model
grows, which is why the line runs `small.en`. Set `STT_MODEL` to `deepdml/faster-whisper-large-v3-turbo-ct2`
in `.env` to hear the difference.

## 6. See it

Score the whole shift first, then open the kitchen display at http://127.0.0.1:8016:

```bash
uv run phoneline review
```

Expect: /taken with no staff: \d of 6 orders/

```sh
uv run phoneline serve
```

Click LB-05. Karen asked for a half and half pizza, so the ticket shows a **Call back** card with the
reason, and nothing went to the kitchen. Click LB-03 to see the cut-in in the transcript. Press **Call
the line with your microphone** and order a pizza yourself. Stop the page with Ctrl-C.

## 7. Prove it

The keyless tests script the model's replies and check the till, the tool loop through Pipecat, the
turn clock and barge-in timing, every recording, and the page's local-only server:

```bash
uv run pytest -q
```

Expect: /\d+ passed/

The proof plays all eight calls through the real pipeline and model. At least 5 of 6 orders must match
the keyed order exactly, both staff calls must go to staff with a reason, and every turn must be timed:

```bash
uv run pytest -m proof -q
```

Expect: 1 passed

Two proof runs on 15 Oct 2026, with `openai/gpt-6-luna` and `small.en` on an M1 laptop:

| Taken with no staff | Sent to staff | Timed turns | p50 turn | p95 turn | Model cost |
| --- | --- | --- | --- | --- | --- |
| 6 of 6 | 2 of 2 | 26 of 26 | 8.5 s | 19.4 s | $0.0049 for 8 calls |
| 5 of 6 | 2 of 2 | 26 of 26 | 8.9 s | 36.0 s | $0.0047 for 8 calls |

The orders are right, and the model costs about $0.0006 a call. The latency is far over the 1.5 s
budget. In the first run the p50 turn split into hear 1.9 s, think 5.1 s and speak 1.1 s. Think includes a second
model call after each tool result, so it is the part to cut first: a faster model, or fewer tool round
trips. The laptop also ran other heavy jobs, which slows Whisper and Kokoro on the same CPU.

Earlier runs failed in ways the till and the tests now hold:

- A model wrote "Diet Coke" in a "2 liter" size, "six piece" wings and "cheese pizzas". The till now
  reads each of those as the menu item.
- A reply with two `add_item` calls got two answers, and one sent the call to staff. Only the last
  tool result of a reply now asks the model again.

## At a startup

Start with one restaurant's menu and its real calls. Record twenty with permission, key each by hand,
and run them on every prompt or model change. Keep speech local while you can: it is free, and no
caller audio leaves the building. Put the menu rules in the till, not the prompt.

## At an enterprise

A chain runs thousands of lines, so latency and cost are per call, not per demo. Speech runs on GPUs
near the phone network, and the model is the fastest one that passes the review. Every location's menu
feeds the same till rules. Staff see each hand-off with its reason and the recording, and those calls
become the next test cases.

## Change it

- Set `SENTENCE_PAUSE` in `src/lucca/pipeline.py` to 0.2 and run LB-01. What does the model answer?
- Remove the `send_to_staff` tool and run LB-05. What reaches the kitchen?
- Switch `STT_MODEL` to large-v3-turbo and review again. Which part of the latency strip grows?
- Add a "two toppings for free" rule to the till, and a call that needs it.

Sources: [SoundHound's restaurant ordering](https://www.soundhound.com/voice-ai-solutions/restaurant-food-ordering/),
the [Pipecat docs](https://docs.pipecat.ai), [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) and [Smart Turn](https://github.com/pipecat-ai/smart-turn).
