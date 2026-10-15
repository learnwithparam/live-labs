"""Settings from the environment, with `.env` as a fallback the shell can override."""

import os
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# List prices in dollars per million tokens: input, cached input, output. The bill from OpenRouter wins
# when it is there; these are the fallback for a provider that does not report cost. The inventory, not
# this file, says which model each system may call.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
}
PORT = 8300
DATA = Path("data")
OUT = Path("out")
AUDIT_DB = OUT / "audit.sqlite"
APP_LOGS = OUT / "app-logs"
DECISIONS = OUT / "decisions.json"
ANSWER_PACK = OUT / "answer-pack.json"
# A lab default so the tests and the README run as written. In production the key sits in a vault the
# log readers cannot open, so a pseudonym in the log cannot be turned back into a customer number.
LAB_PSEUDONYM_KEY = "larkspur-lab-only-pseudonym-key"


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def pseudonym_key() -> bytes:
    load_dotenv()
    return (os.environ.get("AUDIT_PSEUDONYM_KEY") or LAB_PSEUDONYM_KEY).encode()


def llm_settings() -> tuple[str, str]:
    load_dotenv()
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        raise SystemExit("LLM_API_KEY is not set. Copy env.example to .env and add your key.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key
