"""What a phone line does to a voice, so the agent hears callers the way a SIP trunk delivers them.

A US carrier call is G.711 mu-law: 8,000 samples a second, 8 bits each, and only the 300 to 3,400 Hz
band survives. Speech models trained on clean wideband audio hear worse through it, which is why the
lab tests on narrowband audio and not on the clean recordings."""

import numpy as np

PHONE_RATE = 8000
BAND = (300, 3400)
MU = 255


def resample(samples: np.ndarray, rate: int, new_rate: int) -> np.ndarray:
    # Linear interpolation is enough for speech once the band above the new Nyquist is already gone.
    if rate == new_rate:
        return samples.astype(np.float32)
    positions = np.arange(0, len(samples), rate / new_rate)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def band_limit(samples: np.ndarray, rate: int) -> np.ndarray:
    """Cut everything outside the telephone band, the way the handset and the carrier do."""
    spectrum = np.fft.rfft(samples)
    frequencies = np.fft.rfftfreq(len(samples), 1 / rate)
    spectrum[(frequencies < BAND[0]) | (frequencies > BAND[1])] = 0
    return np.fft.irfft(spectrum, len(samples)).astype(np.float32)


def mu_law(samples: np.ndarray) -> np.ndarray:
    """Encode to 8-bit mu-law and decode back: the quantising noise every phone call carries."""
    clipped = np.clip(samples, -1.0, 1.0)
    compressed = np.sign(clipped) * np.log1p(MU * np.abs(clipped)) / np.log1p(MU)
    eight_bit = np.round((compressed + 1) / 2 * 255) / 255 * 2 - 1
    return (np.sign(eight_bit) * np.expm1(np.abs(eight_bit) * np.log1p(MU)) / MU).astype(np.float32)


def phone_line(samples: np.ndarray, rate: int) -> np.ndarray:
    """Float samples at any rate in, 16-bit PCM at 8 kHz out: the audio a SIP participant publishes."""
    narrow = resample(band_limit(samples, rate), rate, PHONE_RATE)
    peak = float(np.max(np.abs(narrow))) or 1.0
    return (mu_law(narrow / peak * 0.8) * 32767).astype(np.int16)
