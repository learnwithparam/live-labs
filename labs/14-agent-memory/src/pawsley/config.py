"""Model and memory settings from the environment, with `.env` as a fallback the shell can override."""

import logging
import os
import warnings
from pathlib import Path

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
# List prices in dollars per million tokens: input, cached input, output. Used only when the provider's
# bill does not come back with the response; OpenRouter returns the billed cost itself.
PRICES = {
    "openai/gpt-6-luna": (0.10, 0.01, 0.50),
    "anthropic/claude-haiku-5.5": (0.10, 0.01, 0.50),
}
# Embeddings run on this machine through FastEmbed (ONNX), so memory search needs no second key.
# The model downloads once, about 70 MB, into out/fastembed.
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_DIMS = 384
OUT = Path("out")


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def quiet_mem0() -> None:
    """Mem0 reads these when it is imported: no telemetry, and its own files stay inside out/."""
    os.environ.setdefault("MEM0_TELEMETRY", "False")
    os.environ.setdefault("MEM0_DIR", str((OUT / "mem0").resolve()))
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str((OUT / "fastembed").resolve()))
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    warnings.filterwarnings("ignore", message="Cannot enable progress bars")
    # spaCy is an optional extra that sharpens keyword search; without it Mem0 warns on every start.
    logging.getLogger("mem0").setLevel(logging.ERROR)
    OUT.mkdir(exist_ok=True)


def llm_settings() -> tuple[str, str, str]:
    load_dotenv()
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        raise SystemExit("LLM_API_KEY is not set. Copy env.example to .env and add your key.")
    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    if model not in PRICES:
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/pawsley/config.py.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model
