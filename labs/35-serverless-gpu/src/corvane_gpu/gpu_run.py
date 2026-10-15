"""The GPU run: check the GPU, install vLLM, fetch the weights once, then run two waves to zero and back.

It runs inside a Kaggle GPU notebook, and in rehearsal on a laptop with the stand-in server. Both use
the same waves, the same burst and the same results file, so the first GPU push is not a debugging run.
A wall-clock guard stops everything early enough to write results, because the GPU quota is weekly.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from corvane_gpu.config import (CONCURRENCY, COLD_START_TIMEOUT_SECONDS, REPLICA_PRICE_PER_SECOND, REQUEST_TIMEOUT_SECONDS,
                                SCALEDOWN_WINDOW_SECONDS, SERVED_MODEL, SERVED_MODEL_REVISION, SERVED_NAME, VLLM_VERSION,
                                VLLM_WHEEL)
from corvane_gpu.golden import golden_set, read_jsonl
from corvane_gpu.replica import Plan, failed_items, run_waves, write_results

# Kaggle stops a notebook at the -t limit we push with (10 minutes). The guard ends the run two minutes
# sooner, so the results file is always written by us, not cut off by them, and the GPU quota is spared.
GUARD_SECONDS = 8 * 60
SECOND_WAVE_ITEMS = 40
PORT = 8000
# Qwen3 thinks out loud unless told not to. A category needs no thinking, and thinking tokens cost seconds.
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}


# A T4 has no bfloat16, so half precision. Left to choose, vLLM picks FlashInfer attention on a T4, which
# compiles its kernels on first use; the Triton backend runs on every GPU without that wait.
VLLM_FLAGS = ["--served-model-name", SERVED_NAME, "--dtype", "half", "--max-model-len", "2048",
              "--max-num-seqs", "32", "--gpu-memory-utilization", "0.85", "--enforce-eager",
              "--enable-prefix-caching", "--attention-backend", "TRITON_ATTN"]


def vllm_command(port: int, weights: str) -> list[str]:
    """`vllm serve` as the Modal app runs it. Eager mode skips graph capture, trading a little speed for a
    much shorter cold start, which matters more when every wake-up pays for it."""
    return ["vllm", "serve", weights, *VLLM_FLAGS, "--host", "127.0.0.1", "--port", str(port)]


def stand_in_command(port: int, startup_seconds: float = 2.0, hang_on: str = "", ramble_on: str = "",
                     latency: float = 0.05) -> list[str]:
    return [sys.executable, "-m", "corvane_gpu.stand_in", "--port", str(port), "--startup-seconds",
            str(startup_seconds), "--hang-on", hang_on, "--ramble-on", ramble_on, "--latency", str(latency)]


def shell(command: list[str], timeout: float) -> tuple[int, str]:
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=max(1.0, timeout))
        return finished.returncode, (finished.stdout + finished.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout:.0f} s"


def check_gpu() -> dict:
    """Refuse to go on without a GPU. A vLLM server falling back to CPU would make every number a lie."""
    import torch

    code, listing = shell(["nvidia-smi", "-L"], 30)
    print(listing, flush=True)
    assert code == 0 and torch.cuda.is_available(), "no GPU: refusing to serve on CPU"
    return {"gpu": torch.cuda.get_device_name(0), "nvidia_smi": listing, "torch": torch.__version__,
            "cuda": torch.version.cuda}


def install_vllm(deadline: float) -> dict:
    """uv resolves and unpacks far faster than pip, and every second here is a GPU second."""
    started = time.time()
    if not shutil.which("uv"):
        shell([sys.executable, "-m", "pip", "install", "-q", "uv"], deadline - time.time())
    # The wheel's own pin, torch==2.11.0, is met by the image's CUDA 12.8 build, so torch is not downloaded again.
    code, output = shell(["uv", "pip", "install", "--system", VLLM_WHEEL], deadline - time.time())
    print(output[-3000:], flush=True)
    assert code == 0, f"vllm {VLLM_VERSION} did not install: {output[-500:]}"
    return {"vllm": VLLM_VERSION, "install_seconds": round(time.time() - started, 1)}


def fetch_weights() -> tuple[str, float]:
    # Downloaded once before the first wave, as a Modal volume holds them for every new container.
    from huggingface_hub import snapshot_download

    started = time.time()
    folder = snapshot_download(SERVED_MODEL, revision=SERVED_MODEL_REVISION)
    return folder, round(time.time() - started, 1)


def plan_for(command: list[str], out_dir: Path, deadline: float, data: Path, about: dict, port: int = PORT,
             request_timeout: float = REQUEST_TIMEOUT_SECONDS, idle_seconds: float = SCALEDOWN_WINDOW_SECONDS,
             cold_start_timeout: float = COLD_START_TIMEOUT_SECONDS, second_wave_items: int = SECOND_WAVE_ITEMS,
             wave_minimum_seconds: float = 150) -> Plan:
    golden = golden_set(data)
    return Plan(server_command=command, port=port,
                waves=[("golden set", golden), ("second wake", golden[:second_wave_items])],
                examples=read_jsonl(data / "examples.jsonl"), served_name=SERVED_NAME, out_dir=out_dir,
                concurrency=CONCURRENCY, request_timeout=request_timeout, cold_start_timeout=cold_start_timeout,
                idle_seconds=idle_seconds, deadline=deadline, price_per_second=REPLICA_PRICE_PER_SECOND,
                wave_minimum_seconds=wave_minimum_seconds, extra_body=NO_THINKING, about=about)


def run_on_gpu(data: Path, out_dir: Path, started: float) -> dict:
    """The Kaggle notebook's whole job. Every phase is bounded by the same deadline."""
    deadline = started + GUARD_SECONDS
    about = {"where": "Kaggle notebook", "model": SERVED_MODEL, "revision": SERVED_MODEL_REVISION, "adapter": None}
    try:
        about.update(check_gpu())
        about.update(install_vllm(deadline - 300))
        weights, about["weights_download_seconds"] = fetch_weights()
    except (AssertionError, OSError) as error:
        results = setup_failed(data, about, str(error), started)
    else:
        results = run_waves(plan_for(vllm_command(PORT, weights), out_dir, deadline - 20, data, about))
    results["kernel_seconds"] = round(time.time() - started, 1)
    write_results(results, out_dir)
    return results


def setup_failed(data: Path, about: dict, reason: str, started: float) -> dict:
    """The results file for a run that never reached a replica. Fail closed: every item is a failure with
    the reason, so a broken install reads as an outage, never as an empty run."""
    golden = golden_set(data)
    reason = f"setup failed before any replica started: {reason[-300:]}"
    return {"about": about, "timeline": [{"at": 0.0, "replicas": 0, "event": "no traffic, no replica"}],
            "waves": [{"name": "golden set", "items": len(golden), "started": False, "reason": reason,
                       "outcomes": failed_items(golden, reason, round(time.time() - started, 3))}],
            "auth": {}, "guard_hit": False, "error": reason,
            "settings": {"concurrency": CONCURRENCY, "request_timeout": REQUEST_TIMEOUT_SECONDS,
                         "cold_start_timeout": COLD_START_TIMEOUT_SECONDS, "idle_seconds": SCALEDOWN_WINDOW_SECONDS}}


def rehearse(data: Path, out_dir: Path, port: int, hang_on: str = "", ramble_on: str = "",
             idle_seconds: float = 2.0, request_timeout: float = 3.0) -> dict:
    """The same run on this laptop against the stand-in, in seconds, with a hang and a ramble to catch."""
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    about = {"where": "rehearsal on this laptop, stand-in server", "model": "stand-in", "gpu": "none"}
    plan = plan_for(stand_in_command(port, hang_on=hang_on, ramble_on=ramble_on), out_dir, time.time() + 120, data,
                    about, port=port, request_timeout=request_timeout, idle_seconds=idle_seconds,
                    cold_start_timeout=20, wave_minimum_seconds=5)
    return run_waves(plan)
