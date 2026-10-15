"""Settings from the environment, with `.env` as a fallback the shell can override."""

import os
from pathlib import Path

LAB = Path(__file__).resolve().parents[2]
DATA = LAB / "data" / "workspaces.json"
OUT = LAB / "out"

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# Streams short answers and calls two read-only tools; the cheapest current model with reliable tools.
DEFAULT_MODEL = "openai/gpt-6-luna"
# The stand-in provider the load test uses, so a thousand requests cost nothing.
STUB_MODEL = "mosswood/stand-in"
# List prices in dollars per million tokens: input, cached input, output. Only a fallback: the agent books
# the cost OpenRouter reports for each call.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
    STUB_MODEL: (0.0, 0.0, 0.0),
}

GATEWAY_PORT = 8310
GATEWAY_ADMIN_PORT = 8311
AGENT_PORT = 8312
PAGE_PORT = 8313
STUB_PORT = 8314

# Envoy, pinned to a release more than two weeks old, by tag and by digest.
ENVOY_IMAGE = "envoyproxy/envoy:v1.39.1@sha256:57e14a549d7bd43c8d3f6d03e8cfa653e037d4b38e133acd9b54f38c524401b4"
GATEWAY_CONTAINER = "mosswood-gateway"

# The web app's tokens: who signs them, who they are for, and how long they live.
ISSUER = "https://app.mosswood.example"
AUDIENCE = "assistant-api"
TOKEN_SECONDS = 300

# Rate limits are counted in one window: a bucket refills this often.
WINDOW_SECONDS = 10
USER_REQUESTS_PER_WINDOW = 5

# The agent's own deadlines, so a stuck model ends in an error event and never a hang.
FIRST_TOKEN_SECONDS = 20
IDLE_SECONDS = 30


def load_dotenv(path: Path = LAB / ".env") -> None:
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
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/mosswood/config.py.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model


def stub_settings() -> tuple[str, str, str]:
    return f"http://127.0.0.1:{STUB_PORT}/v1", "stand-in", STUB_MODEL
