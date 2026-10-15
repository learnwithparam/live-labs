"""Model and registry settings from the environment, with `.env` as a fallback the shell can override."""

import os
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
# List prices in dollars per million tokens: input, cached input, output. The bill from OpenRouter wins
# when it is there; these are the fallback for a provider that does not report cost.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
    "openai/gpt-6-luna-pro": (0.10, 0.01, 0.50),
    "openai/gpt-5.6-luna": (0.20, 0.02, 1.20),
}
# The release console. Langfuse itself listens on 8270, set in compose.yaml.
PORT = 8271
PROMPT_NAME = "support-bot"
# How long the bot trusts its copy of the production prompt. A label move reaches it within this window.
CACHE_TTL_SECONDS = 10
# The golden set score a version needs before it can hold the production label without an override.
PASS_BAR = 0.85
# The local stack's keys from compose.yaml. They only open a Langfuse on 127.0.0.1.
LANGFUSE_DEFAULTS = {"LANGFUSE_BASE_URL": "http://localhost:8270",
                     "LANGFUSE_PUBLIC_KEY": "pk-lf-driftwell-local",
                     "LANGFUSE_SECRET_KEY": "sk-lf-driftwell-local"}


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
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/driftwell/config.py so each reply is costed.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model


def langfuse_settings() -> dict[str, str]:
    load_dotenv()
    return {name: os.environ.get(name) or default for name, default in LANGFUSE_DEFAULTS.items()}
