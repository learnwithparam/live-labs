"""Settings from the environment, with `.env` as a fallback the shell can override."""

import json
import os
from pathlib import Path

DATA = Path("data")
OUT = Path("out")
SERVICE_NAME = "quillmark-helpdesk"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
DEFAULT_OTLP_ENDPOINT = "http://127.0.0.1:8282/v1/traces"
DEFAULT_JAEGER_URL = "http://127.0.0.1:8281"
# Nothing listens here: the month sends one run's spans to it to play a collector being redeployed.
DOWN_COLLECTOR = "http://127.0.0.1:8289/v1/traces"
# List prices in dollars per million tokens: input, cached input, output. Only a fallback: OpenRouter
# returns the billed cost of each generation, and that billed cost is the invoice.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
}
TIMEOUT = 90  # seconds per model call
PARALLEL = 4  # runs in flight at once, like a busy afternoon on the helpdesk
PORT = 8280


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def otlp_endpoint() -> str:
    load_dotenv()
    return os.environ.get("OTLP_ENDPOINT") or DEFAULT_OTLP_ENDPOINT


def jaeger_url() -> str:
    load_dotenv()
    return os.environ.get("JAEGER_URL") or DEFAULT_JAEGER_URL


def llm_settings() -> tuple[str, str, str]:
    load_dotenv()
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        raise SystemExit("LLM_API_KEY is not set. Copy env.example to .env and add your key.")
    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    if model not in PRICES:
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/quillmark/config.py.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model


def accounts() -> dict:
    return json.loads((DATA / "accounts.json").read_text())


def runs() -> list[dict]:
    return [json.loads(line) for line in (DATA / "runs.jsonl").read_text().splitlines() if line.strip()]
