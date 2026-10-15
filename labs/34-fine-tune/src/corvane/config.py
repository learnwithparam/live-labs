"""Settings: the large model from the environment, the small model pinned, and every price with its source."""

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

# The small model: Qwen3 0.6B, Apache 2.0, not gated. Pinned to one commit so a re-upload cannot change it.
SMALL_MODEL = "Qwen/Qwen3-0.6B"
SMALL_MODEL_REVISION = "c1899de289a04d12100db370d81485cdf75e47ca"

# The small model runs on an Apple M1 with 16 GB. AWS rents that exact machine as mac2.metal:
# $0.650 an hour on demand in us-east-1, read on instances.vantage.sh/aws/ec2/mac2.metal on 15 Oct 2026.
# AWS bills it per second with a 24-hour minimum allocation.
SMALL_MODEL_HOURLY = 0.650
SMALL_MODEL_HOST = "AWS mac2.metal (Apple M1, 16 GB), $0.650/hour on demand, us-east-1, read 15 Oct 2026"

PORT = 8340

# The release rules, set from honest runs and not wished for. Each one is pinned in tests/test_release.py,
# so changing a number here is a decision someone has to make on purpose.
# The tuned model alone may trail the large one by this much on the held-out test, and no category may
# fall below the floor in recall. This gate holds the plain tuned model.
MARGIN = 0.03
CLASS_FLOOR = 0.60
# The cascade: the small model labels the items it is sure of and sends the rest to the large model.
# It may trail the large model by one point, overall and on the items it keeps, and must label 70% itself.
CASCADE_MARGIN = 0.01
COVERAGE_FLOOR = 0.70
# The threshold is read from the validation split: the strictest one that still sends at most this
# share of validation items to the large model. The test split only checks the choice.
ROUTE_BUDGET = 0.20
THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)


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
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/corvane/config.py so each item is costed.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model
