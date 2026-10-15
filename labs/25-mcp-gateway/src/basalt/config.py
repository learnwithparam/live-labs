"""Settings: the model from the environment (with `.env` as a fallback), prices, ports and paths."""

import os
from pathlib import Path

LAB = Path(__file__).resolve().parents[2]
DATA = LAB / "data"
OUT = LAB / "out"
CATALOGUE = DATA / "approved-servers.toml"

HOST = "127.0.0.1"
# The lab owns ports 8250 to 8259. Staff agents only ever learn the gateway's.
GATEWAY_PORT = 8250
SERVER_PORTS = {"payments": 8251, "helpdesk": 8252, "deploys": 8253, "merchants": 8254, "snippets": 8255}
GUARD_PORT = 8256
READINESS_PORT = 8257
CONSOLE_PORT = 8259

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
# List prices in dollars per million tokens: input, cached input, output. The provider's bill wins when
# OpenRouter reports it; these are the fallback for other endpoints.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
    "openai/gpt-6-luna-pro": (0.10, 0.01, 0.50),
    "openai/gpt-5.6-luna": (0.20, 0.02, 1.20),
}


def server_url(server: str) -> str:
    return f"http://{HOST}:{SERVER_PORTS[server]}/mcp"


GATEWAY_URL = f"http://{HOST}:{GATEWAY_PORT}/mcp"


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
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model
