"""Settings from the environment, with `.env` as a fallback the shell can override."""

import os
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# The strong tier, the one every call used before the router.
DEFAULT_MODEL = "openai/gpt-6-luna"
# The cheap tier: about a sixth of the strong model's bill on a short answer, with reasoning switched off.
# With reasoning on it thinks for hundreds of tokens and costs more than the strong model.
CHEAP_MODEL = "qwen/qwen3.7-flash"
# Where the strong tier falls back when its provider fails: a different vendor at the same list price.
BACKUP_MODEL = "anthropic/claude-haiku-5.5"
# List prices in dollars per million tokens: input, cached input, output. Only a fallback: the gateway
# books the cost OpenRouter reports for each call, and the proof fails if any call was estimated.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
    "qwen/qwen3.7-flash": (0.03, 0.03, 0.13),
    "anthropic/claude-haiku-5.5": (0.10, 0.01, 0.50),
}
PAGE_PORT = 8260
PROXY_PORT = 8261
# Nothing listens here. Pointing a deployment at it is how `--cheap-down` stages a provider outage.
DEAD_PORT = 8269


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
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/fernway/config.py.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model
