"""The Modal app is the deployment the GPU run measured: same flags, scale to zero, the key from a secret."""

import ast
from pathlib import Path

import modal_app
from corvane_gpu.config import GPU_TYPE, SCALEDOWN_WINDOW_SECONDS
from corvane_gpu.gpu_run import VLLM_FLAGS, vllm_command

SOURCE = ast.parse(Path(modal_app.__file__).read_text())


def keywords_of(call_name: str) -> dict:
    for node in ast.walk(SOURCE):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == call_name:
            return {keyword.arg: ast.unparse(keyword.value) for keyword in node.keywords}
    raise AssertionError(f"{call_name} not found")


def test_the_endpoint_scales_to_zero_on_the_measured_gpu():
    function = keywords_of("app.function")
    assert function["min_containers"] == "0"
    assert function["scaledown_window"] == "SCALEDOWN_WINDOW_SECONDS" and SCALEDOWN_WINDOW_SECONDS == 30
    assert function["gpu"] == "GPU_TYPE" and GPU_TYPE == "T4"
    assert "VLLM_API_KEY" in function["secrets"]
    assert keywords_of("modal.web_server")["port"] == "PORT"


def test_modal_and_the_gpu_run_serve_with_the_same_flags():
    assert modal_app.VLLM_FLAGS is VLLM_FLAGS
    assert vllm_command(8000, "/weights")[3:3 + len(VLLM_FLAGS)] == VLLM_FLAGS
    assert "--api-key" not in VLLM_FLAGS
