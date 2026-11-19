"""Where the files live, the signed numbers, the two model slots and every limit the service holds."""

import os
from pathlib import Path

LAB = Path(__file__).resolve().parents[2]
DATA = LAB / "data"
OUT = LAB / "out"

# The numbers the architecture RFC signed: the work order must be ready within this p95, and the
# step budgets add up to 7200 ms inside it. A test pins both, so a drift shows.
P95_TARGET_MS = 8000
STEP_BUDGETS_MS = {"intake": 200, "extraction": 3500, "vendor_match": 600, "drafting": 2500, "sending": 400}

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
PRIMARY_MODEL = "nvidia/nemotron-3.5-lightning"
FALLBACK_MODEL = "openai/gpt-6-luna"
# List prices in dollars per million tokens: input, then output. The ledger costs each call from these
# and is then checked against what the provider billed.
PRICES = {PRIMARY_MODEL: (0.07, 0.20), FALLBACK_MODEL: (0.10, 0.50)}

# The model gets the extraction budget to send its next byte. Past that the call is a failure, so the
# fallback still has time left inside the signed p95.
PROVIDER_TIMEOUT_S = STEP_BUDGETS_MS["extraction"] / 1000
# Three failures in a row open the breaker; after the cooldown one probe call may close it again.
BREAKER_FAILURES = 3
BREAKER_COOLDOWN_S = 20.0
# An operator may raise a client's limit, but never past this: a typo must not open the door.
HARD_CEILING_PER_MINUTE = 600
# The ledger may differ from the provider's bill by this share of the bill.
LEDGER_TOLERANCE = 0.05
# The provider's billing record can lag the call by a few seconds.
BILLING_WAIT_S = 30
MAX_BODY_BYTES = 16_000

API_PORT = 8371
PORTAL_PORT = 8372


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and not name.strip().startswith("#"):
            os.environ.setdefault(name.strip(), value.strip())


def llm_settings() -> tuple[str, str]:
    load_dotenv()
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        raise SystemExit("LLM_API_KEY is not set. Copy env.example to .env and add your key.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key


def model_slots() -> tuple[str, str]:
    """The primary and fallback model ids, from the settings file when it names them."""
    load_dotenv()
    return os.environ.get("LLM_MODEL") or PRIMARY_MODEL, os.environ.get("LLM_FALLBACK_MODEL") or FALLBACK_MODEL
