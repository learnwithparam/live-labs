"""Settings from the environment, with `.env` as a fallback the shell can override."""

import os
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# Measured on 22 Oct 2026 with this lab's prompt: the first token of a reply in 0.8 s at the median, where
# the other small models took 2.4 s or more. On a phone that gap is dead air, so speed picks the model.
DEFAULT_MODEL = "google/gemini-3.5-flash-lite"
# List prices in dollars per million tokens: input, cached input, output. LiveKit's OpenAI plugin keeps
# the token counts of each reply but not the bill, so a call's cost is its tokens times these prices.
PRICES = {
    "google/gemini-3.5-flash-lite": (0.30, 0.03, 2.50),
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
    "anthropic/claude-haiku-5.5": (0.10, 0.01, 0.50),
}

AGENT_NAME = "northfield-front-desk"

# Speech runs on LiveKit Inference with the models LiveKit's agent-starter-python picks: AssemblyAI hears,
# Fish Audio speaks. Both are billed to the LiveKit project, not to the model key.
STT_MODEL = "assemblyai/universal-3-6-pro"
TTS_MODEL = "fishaudio/s2.1-pro"
AGENT_VOICE = "fa4c9eb3dccc4806b382b40d61c6b10a"

DATA = Path(__file__).resolve().parents[2] / "data"
OUT = Path("out")


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def livekit_settings() -> tuple[str, str, str]:
    """The LiveKit Cloud project every call's room lives in. Its URL ends in .livekit.cloud."""
    load_dotenv()
    names = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"{', '.join(missing)} not set. Copy them from your LiveKit Cloud project into .env.")
    return tuple(os.environ[name] for name in names)


def llm_settings() -> tuple[str, str, str]:
    load_dotenv()
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        raise SystemExit("LLM_API_KEY is not set. Copy env.example to .env and add your key.")
    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    if model not in PRICES:
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/northfield/config.py.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model


def cost_of(model: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> float:
    price_in, price_cached, price_out = PRICES[model]
    fresh = prompt_tokens - cached_tokens
    return (fresh * price_in + cached_tokens * price_cached + completion_tokens * price_out) / 1_000_000
