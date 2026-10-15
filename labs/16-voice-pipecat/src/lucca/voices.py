"""Record the scripted callers once, with the same local text-to-speech the agent speaks with.

The proof needs callers who say the same words the same way on every run, or a changed score could be
the caller and not the code. So each line is synthesised once, at a different Kokoro voice per caller,
and the wav files are committed. Run this again only when data/calls.json changes.
"""

import wave
from pathlib import Path

import numpy as np

from lucca.caller import AUDIO_DIR, CALLER_RATE, load_calls

KOKORO_DIR = Path.home() / ".cache" / "pipecat" / "kokoro-onnx"  # where Pipecat's Kokoro service keeps its model
QUIET = 0.01  # below this amplitude a sample counts as silence when trimming the ends
EDGE_SECONDS = 0.1  # silence kept at each end, so the first syllable is not clipped


def to_caller_audio(samples: np.ndarray, rate: int) -> bytes:
    """Trim the silence Kokoro leaves at both ends, resample to 16 kHz and store as 16-bit PCM: the
    smallest form the pipeline accepts, which keeps the committed files small."""
    loud = np.flatnonzero(np.abs(samples) > QUIET)
    edge = int(EDGE_SECONDS * rate)
    if loud.size:
        samples = samples[max(0, loud[0] - edge): loud[-1] + edge]
    positions = np.arange(0, len(samples), rate / CALLER_RATE)
    resampled = np.interp(positions, np.arange(len(samples)), samples)
    return (np.clip(resampled, -1, 1) * 32767).astype(np.int16).tobytes()


def record_calls() -> list[Path]:
    from kokoro_onnx import Kokoro

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    missing = [(call, line_number, script_line) for call in load_calls()
               for line_number, script_line in enumerate(call["lines"], 1)
               if not (AUDIO_DIR / f"{call['id']}-{line_number}.wav").exists()]
    if not missing:
        return []
    speaker = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
    written = []
    for call, line_number, script_line in missing:
        target = AUDIO_DIR / f"{call['id']}-{line_number}.wav"
        samples, rate = speaker.create(script_line["say"], voice=call["voice"], speed=1.0, lang="en-us")
        with wave.open(str(target), "wb") as recording:
            recording.setnchannels(1)
            recording.setsampwidth(2)
            recording.setframerate(CALLER_RATE)
            recording.writeframes(to_caller_audio(samples, rate))
        written.append(target)
        print(f"recorded {target}")
    return written
