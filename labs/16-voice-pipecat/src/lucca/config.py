"""Model and voice settings from the environment, with `.env` as a fallback the shell can override."""

import os
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# A phone caller waits for the model's first word on every turn, so the model must be fast, and it must
# read the order back before placing it. A faster model placed orders before the read-back; this one
# reads back every time and starts answering in under a second.
DEFAULT_MODEL = "anthropic/claude-haiku-5.5"
# List prices in dollars per million tokens: input, cached input, output. OpenRouter's billed cost wins
# when it is reported; these are the fallback for other OpenAI-compatible endpoints.
PRICES = {
    "nvidia/nemotron-3.5-lightning": (0.07, 0.007, 0.20),
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
    "anthropic/claude-haiku-5.5": (0.10, 0.01, 0.50),
    "google/gemini-3.1-flash-lite": (0.25, 0.025, 1.50),
}
# Speech to text runs on this Mac's GPU through MLX. base.en hears a five second order in about 0.2 s;
# small.en hears better and takes about three times as long, on every turn.
DEFAULT_STT_MODEL = "mlx-community/whisper-base.en-mlx"
AGENT_VOICE = "af_bella"  # Kokoro voice for the ordering line


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def llm_settings() -> tuple[str, str, str]:
    load_dotenv()
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        raise SystemExit("LLM_API_KEY is not set. Copy env.example to .env and add your key.")
    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model


def stt_model() -> str:
    load_dotenv()
    return os.environ.get("STT_MODEL") or DEFAULT_STT_MODEL
