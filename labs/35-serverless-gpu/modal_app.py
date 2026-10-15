"""Corvane's category endpoint on Modal: vLLM on a T4 that runs only while there is work.

    uv run modal secret create corvane-vllm VLLM_API_KEY=<a long random key>
    uv run modal deploy modal_app.py

This file is the deployment the Kaggle run measured. It uses the same vLLM version, flags, model
commit and scaledown window, imported from the package, so the two cannot drift apart.
"""

import subprocess

import modal

from corvane_gpu.config import (CONTAINER_CPU_CORES, CONTAINER_MEMORY_GIB, GPU_TYPE, SCALEDOWN_WINDOW_SECONDS,
                                SERVED_MODEL, SERVED_MODEL_REVISION, TORCH_INDEX, VLLM_WHEEL)
from corvane_gpu.gpu_run import VLLM_FLAGS

PORT = 8000
WEIGHTS = "/weights"

# The image installs vLLM once at build time; a container start then only loads weights onto the GPU.
# torch and vLLM are the CUDA 12 builds the Kaggle run measured, not PyPI's CUDA 13 defaults.
image = (modal.Image.debian_slim(python_version="3.12")
         .uv_pip_install("torch==2.11.0", index_url=TORCH_INDEX)
         .uv_pip_install(VLLM_WHEEL, "huggingface_hub>=0.34")
         .env({"HF_HOME": WEIGHTS})
         .add_local_python_source("corvane_gpu"))

# A volume keeps the weights between containers, so a cold start never downloads them again.
weights = modal.Volume.from_name("corvane-weights", create_if_missing=True)
app = modal.App("corvane-category", image=image)


@app.function(
    gpu=GPU_TYPE,
    cpu=CONTAINER_CPU_CORES,
    memory=CONTAINER_MEMORY_GIB * 1024,
    # Zero replicas when nobody is calling: no traffic, no bill. The price is a cold start on the next call.
    min_containers=0,
    # An idle replica waits this long for more work before it shuts down, and that wait is billed.
    scaledown_window=SCALEDOWN_WINDOW_SECONDS,
    volumes={WEIGHTS: weights},
    # VLLM_API_KEY comes from a Modal secret, so the endpoint answers only callers who hold the key.
    secrets=[modal.Secret.from_name("corvane-vllm", required_keys=["VLLM_API_KEY"])],
    timeout=60 * 60,
)
# One replica takes many requests at once; vLLM batches them on the GPU.
@modal.concurrent(max_inputs=32)
@modal.web_server(port=PORT, startup_timeout=10 * 60)
def serve() -> None:
    from huggingface_hub import snapshot_download

    folder = snapshot_download(SERVED_MODEL, revision=SERVED_MODEL_REVISION)
    weights.commit()
    # A fine-tuned LoRA adapter would be served here too, with --enable-lora and --lora-modules.
    subprocess.Popen(["vllm", "serve", folder, *VLLM_FLAGS, "--host", "0.0.0.0", "--port", str(PORT)])
