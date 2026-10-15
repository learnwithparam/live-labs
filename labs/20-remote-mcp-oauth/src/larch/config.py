"""Settings: the three local addresses, and the model from the environment with `.env` as a fallback."""

import os
from pathlib import Path

# Three processes' worth of servers, each on its own port, all on this machine. The page is Larch's
# web app, the MCP server is the resource, and the authorization server issues and checks tokens.
PAGE_PORT = 8200
MCP_PORT = 8201
AUTH_PORT = 8202
# The naive server, one admin key for everyone, runs beside it so the review can compare the two.
SHARED_KEY_PORT = 8203
HOST = "127.0.0.1"
ISSUER = f"http://{HOST}:{AUTH_PORT}"
RESOURCE = f"http://{HOST}:{MCP_PORT}/mcp"
SHARED_KEY_URL = f"http://{HOST}:{SHARED_KEY_PORT}/mcp"
# Where the assistant's OAuth client says the browser comes back to after consent.
CALLBACK = f"http://{HOST}:{PAGE_PORT}/callback"

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
# List prices in dollars per million tokens: input, cached input, output. Used only when the provider's
# bill does not come back with the response; OpenRouter returns the billed cost itself.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
    "anthropic/claude-haiku-5.5": (0.10, 0.01, 0.50),
}


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
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/larch/config.py.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model
