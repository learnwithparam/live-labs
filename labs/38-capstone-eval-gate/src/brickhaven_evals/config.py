"""Where the files live, the release thresholds and the model settings."""

import os
from pathlib import Path

LAB = Path(__file__).resolve().parents[2]
DATA = LAB / "data"
OUT = LAB / "out"
RUNS = LAB / "runs"
CANDIDATES = LAB / "candidates"
# The lowest pass rate each field may have, as the architecture RFC's eval plan signed it.
THRESHOLDS = {"trade": 0.95, "urgency": 0.90, "unit": 0.95, "safety_flag": 1.0, "access_window": 0.85}
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning"
TIMEOUT = 60  # seconds per model call
WORKERS = 6
PORT = 8380


def load_dotenv(path: Path = LAB / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


def llm_settings() -> tuple[str, str]:
    load_dotenv()
    key = os.environ.get("LLM_API_KEY", "")
    if not key:
        raise SystemExit("LLM_API_KEY is not set. Copy env.example to .env and add your key.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key
