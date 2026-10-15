"""Model settings from the environment, with `.env` as a fallback the shell can override."""

import os
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# The assistant on every property page is a small open model: it answers millions of page views, so
# it must be cheap. The rails model reads only short policy questions, so it can be a stronger one.
DEFAULT_MODEL = "ibm-granite/granite-4.2-8b"
DEFAULT_RAIL_MODEL = "openai/gpt-6-luna"
# List prices in dollars per million tokens: input, cached input, output. Used only when the provider
# does not report the billed cost itself.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
    "openai/gpt-6-luna-pro": (0.10, 0.01, 0.50),
    "openai/gpt-oss-safeguard-20b": (0.075, 0.0375, 0.30),
    "qwen/qwen3.7-flash": (0.03, 0.006, 0.13),
    "ibm-granite/granite-4.2-8b": (0.06, 0.06, 0.25),
}


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def llm_settings() -> dict:
    load_dotenv()
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        raise SystemExit("LLM_API_KEY is not set. Copy env.example to .env and add your key.")
    settings = {"base_url": os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, "api_key": key,
                "model": os.environ.get("LLM_MODEL") or DEFAULT_MODEL,
                "rail_model": os.environ.get("RAIL_MODEL") or DEFAULT_RAIL_MODEL}
    for name in (settings["model"], settings["rail_model"]):
        if name not in PRICES:
            raise SystemExit(f"No price for {name}. Add it to PRICES in src/brightdoor/config.py.")
    return settings
