"""Settings and the numbers the team signed: the step budgets, the p95 target and the proof thresholds."""

import os
from pathlib import Path

LAB = Path(__file__).resolve().parents[2]
DATA = LAB / "data"
OUT = LAB / "out"

# The latency budget from the architecture RFC, in milliseconds per step. A test pins that they add up
# to TOTAL_BUDGET_MS and stay inside P95_TARGET_MS, so nobody changes one number and forgets the other.
STEP_BUDGET_MS = {"intake": 200, "extraction": 3500, "vendor_match": 600, "drafting": 2500, "sending": 400}
TOTAL_BUDGET_MS = 7200
P95_TARGET_MS = 8000
STEPS = tuple(STEP_BUDGET_MS)

# The proof thresholds. Two of the 16 messages can never be dispatched (no flat named, no vendor for
# pests), so the best possible rate is 14 of 16; 0.8 leaves room for one model slip, not more.
COMPLETION_THRESHOLD = 0.8
MAX_ATTEMPTS = 3
RETRY_PAUSE_MS = 150
WORKERS = 4

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning"
DEFAULT_OTLP_ENDPOINT = "http://127.0.0.1:8372/v1/traces"
DEFAULT_JAEGER_URL = "http://127.0.0.1:8371"
SERVICE_NAME = "brickhaven-slice"
PORT = 8370
MODES = ("naive", "slice")


def load_dotenv(path: Path = LAB / ".env") -> None:
    """Read `.env` as a fallback: a variable already in the shell wins."""
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


def otlp_endpoint() -> str:
    load_dotenv()
    return os.environ.get("OTLP_ENDPOINT") or DEFAULT_OTLP_ENDPOINT


def jaeger_url() -> str:
    load_dotenv()
    return os.environ.get("JAEGER_URL") or DEFAULT_JAEGER_URL
