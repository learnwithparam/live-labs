"""Settings from the environment, with `.env` as a fallback the shell can override."""

import os
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
# List prices in dollars per million tokens: input, cached input, output. LiveKit's OpenAI plugin keeps
# the token counts of each reply but not the bill, so a call's cost is its tokens times these prices.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
    "anthropic/claude-haiku-5.5": (0.10, 0.01, 0.50),
}

# A local `livekit-server --dev` prints these development credentials; LiveKit Cloud gives real ones.
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://127.0.0.1:7880")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "secret")
AGENT_NAME = "northfield-front-desk"

# Speech runs on this machine: faster-whisper hears, Kokoro speaks.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base.en")
KOKORO_DIR = Path(os.environ.get("KOKORO_DIR", Path.home() / ".cache" / "kokoro-onnx"))
AGENT_VOICE = "af_heart"

DATA = Path(__file__).resolve().parents[2] / "data"
OUT = Path("out")


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
    if model not in PRICES:
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/northfield/config.py.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model


def cost_of(model: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> float:
    price_in, price_cached, price_out = PRICES[model]
    fresh = prompt_tokens - cached_tokens
    return (fresh * price_in + cached_tokens * price_cached + completion_tokens * price_out) / 1_000_000
