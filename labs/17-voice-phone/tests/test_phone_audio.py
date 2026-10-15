"""The callers' audio is what a US phone line delivers: 8 kHz, telephone band only, mu-law noise."""

import wave

import numpy as np

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
