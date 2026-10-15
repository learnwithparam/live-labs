"""Model settings from the environment, with `.env` as a fallback the shell can override."""

import os
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
DEFAULT_DECISION_URL = "http://127.0.0.1:8099"
DEFAULT_DECISION_MODEL = "StrandsAgents/strands-decider-2B-hobson-v19"


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
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, os.environ.get("LLM_MODEL") or DEFAULT_MODEL


def decision_settings() -> tuple[str, str, str]:
    load_dotenv()
    return (os.environ.get("DECISION_BASE_URL") or DEFAULT_DECISION_URL,
            os.environ.get("DECISION_API_KEY", ""),
            os.environ.get("DECISION_MODEL") or DEFAULT_DECISION_MODEL)
