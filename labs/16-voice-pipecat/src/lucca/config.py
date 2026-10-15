"""Model and voice settings from the environment, with `.env` as a fallback the shell can override."""

import os
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
# List prices in dollars per million tokens: input, cached input, output. OpenRouter's billed cost wins
# when it is reported; these are the fallback for other OpenAI-compatible endpoints.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
    "anthropic/claude-haiku-5.5": (0.10, 0.01, 0.50),
    "google/gemini-3.1-flash-lite": (0.25, 0.025, 1.50),
}
# Speech to text runs on this machine with faster-whisper. small.en answers in a fraction of a second on
# a laptop CPU; large-v3-turbo hears better and costs about three times as long per turn.
DEFAULT_STT_MODEL = "small.en"
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
