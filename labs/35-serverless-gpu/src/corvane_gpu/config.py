"""Settings: the served model pinned, the hosted model from the environment, and every price with its source."""

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

# The served model: Qwen3 0.6B, Apache 2.0, not gated, pinned to one commit. It is the base the
# fine-tuning lab trains its adapter on, so the adapter can be served on this same endpoint later.
SERVED_MODEL = "Qwen/Qwen3-0.6B"
SERVED_MODEL_REVISION = "c1899de289a04d12100db370d81485cdf75e47ca"
SERVED_NAME = "corvane-category"
# vLLM 0.20.2 pins torch 2.11.0, the version the Kaggle GPU image has, built there for CUDA 12.8. The PyPI
# wheel is built for CUDA 13 and fails at import with "libcudart.so.13: cannot open shared object file",
# so the install takes vLLM's own CUDA 12 build from its GitHub release and keeps the image's torch.
VLLM_VERSION = "0.20.2"
VLLM_WHEEL = (f"https://github.com/vllm-project/vllm/releases/download/v{VLLM_VERSION}/"
              f"vllm-{VLLM_VERSION}+cu129-cp38-abi3-manylinux_2_31_x86_64.whl")
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"

# GPU seconds are priced at Modal's published per-second rate for the GPU the run measured on.
GPU_TYPE = "T4"
GPU_PRICE_PER_SECOND = 0.000164
# Modal also bills the container's CPU and memory for every second the GPU is up. modal_app.py asks for
# this size, so the replica's price per second is all three, not the GPU alone.
CONTAINER_CPU_CORES = 2
CONTAINER_MEMORY_GIB = 8
CPU_PRICE_PER_CORE_SECOND = 0.0000131
MEMORY_PRICE_PER_GIB_SECOND = 0.00000222
REPLICA_PRICE_PER_SECOND = round(GPU_PRICE_PER_SECOND + CONTAINER_CPU_CORES * CPU_PRICE_PER_CORE_SECOND
                                 + CONTAINER_MEMORY_GIB * MEMORY_PRICE_PER_GIB_SECOND, 8)
PRICE_SOURCE = ("Modal, read 15 Oct 2026 at modal.com/pricing: Nvidia T4 $0.000164/s, CPU $0.0000131 per core/s, "
                "memory $0.00000222 per GiB/s")

# How long an idle replica stays up before it scales to zero. The Kaggle run and modal_app.py use the same value.
SCALEDOWN_WINDOW_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 30
COLD_START_TIMEOUT_SECONDS = 420
CONCURRENCY = 16
HOSTED_CONCURRENCY = 8

PAGE_PORT = 8350
GPU_RUN = Path("data/gpu-run/results.json")
RECORDED_HOSTED_RUN = Path("data/hosted-run.json")
OUT = Path("out")
HOSTED_RUN = OUT / "hosted.json"
DECISIONS = OUT / "decisions.jsonl"


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
        raise SystemExit(f"No price for {model}. Add it to PRICES in src/corvane_gpu/config.py so each item is costed.")
    return os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL, key, model
