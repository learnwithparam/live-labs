"""Settings, prices and ports for Meterline's nightly invoice check, with `.env` as a fallback the shell can override."""

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

# Compute prices, read from the AWS price list API for us-east-1. Lambda's offer file was published
# 2026-10-14 and the EC2 one 2026-10-12; both are linked in the README.
LAMBDA_ARM_PER_GB_SECOND = 0.0000133334
LAMBDA_PER_REQUEST = 0.20 / 1_000_000
DURABLE_PER_OPERATION = 8.00 / 1_000_000
DURABLE_PER_GB_WRITTEN = 0.25
VM_NAME = "EC2 t4g.small, on demand, Linux"
VM_PER_HOUR = 0.0168
PRICES_CHECKED = "2026-10-15"
FUNCTION_MEMORY_GB = 0.5

PERIOD = "2026-09"
# Finance lets the agent fix an invoice by up to this much on its own; a bigger change waits for a person.
UNATTENDED_LIMIT = 1500.00
FUNCTION_NAME = "nightly-invoice-check"

PAGE_PORT = 8330
RUNNER_PORT = 8331
HOST_PORT = 8332
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
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/meterline/config.py so each call is costed.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model
