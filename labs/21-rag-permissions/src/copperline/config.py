"""Settings from the environment, with `.env` as a fallback the shell can override."""

import os
from pathlib import Path

DATA = Path("data")
OUT = Path("out")
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
DEFAULT_FGA_URL = "http://127.0.0.1:8129"
# List prices in dollars per million tokens: input, cached input, output. Used only when the provider's
# bill does not come back with the response; OpenRouter returns the billed cost itself.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
}
TIMEOUT = 90  # seconds per model call
PARALLEL = 6  # answers written at once; more only queues at the provider
PORT = 8121


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def fga_url() -> str:
    load_dotenv()
    return os.environ.get("FGA_API_URL") or DEFAULT_FGA_URL


def llm_settings() -> tuple[str, str, str]:
    load_dotenv()
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        raise SystemExit("LLM_API_KEY is not set. Copy env.example to .env and add your key.")
    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    if model not in PRICES:
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/copperline/config.py.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model


def model_client():
    from openai import AsyncOpenAI

    base_url, api_key, model_name = llm_settings()
    return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2), model_name
