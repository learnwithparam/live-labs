"""Record every caller's lines once, as they would sound down a phone line.

The recordings are committed, so every run plays the same audio and a change in the score comes from
the agent, not from a new take of the caller. A " ... " in a line is a pause of PAUSE seconds: the
caller stopping to think mid-sentence, the moment a turn detector has to get right."""

import wave

import numpy as np

from northfield.caller import line_audio, scripts
from northfield.phone_audio import PHONE_RATE, phone_line
from northfield.speech import speak

PAUSE = 1.2


def record_line(text: str, voice: str) -> np.ndarray:
    pieces = []
    for part in text.split(" ... "):
        samples, rate = speak(part, voice)
        pieces += [samples, np.zeros(int(rate * PAUSE), dtype=np.float32)]
    # Drop the pause after the last piece; the caller's own silence comes from waiting, not the file.
    return phone_line(np.concatenate(pieces[:-1]), rate)


def record_all(force: bool = False) -> list[str]:
    written = []
    for call_id, script in scripts().items():
        for number, line in enumerate(script["lines"], start=1):
            path = line_audio(call_id, number)
            if path.exists() and not force:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as recording:
                recording.setnchannels(1)
                recording.setsampwidth(2)
                recording.setframerate(PHONE_RATE)
                recording.writeframes(record_line(line["say"], script["voice"]).tobytes())
            written.append(path.name)
    return written
