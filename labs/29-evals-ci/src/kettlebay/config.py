"""Model settings from the environment, with `.env` as a fallback the shell can override."""

import os
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
# List prices in dollars per million tokens: input, cached input, output. The bill from OpenRouter wins
# when it is there; these are the fallback for a provider that does not report cost.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
    "qwen/qwen3.7-flash": (0.03, 0.006, 0.13),
}
# Every golden case runs REPEATS times per release, and passes when it passes in at least PASS_SHARE
# of them. The model is not deterministic: one run can wobble, three runs that mostly agree is a
# measurement. With 3 runs, a case needs 2.
REPEATS = 3
PASS_SHARE = 2 / 3
WORKERS = 6
TIMEOUT = 60  # seconds per model call
PORT = 8290


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def llm_settings() -> tuple[str, str]:
    load_dotenv()
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        raise SystemExit("LLM_API_KEY is not set. Copy env.example to .env and add your key.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key


def check_priced(model: str) -> None:
    if model not in PRICES:
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/kettlebay/config.py so each run is costed.")
