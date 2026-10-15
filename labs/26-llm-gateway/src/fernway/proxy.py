"""Write the LiteLLM proxy's config.yaml and run the proxy as a real local process.

Every team's app talks to this one address with its own key. The tiers, the fallbacks and the hooks
live in the config, so the platform team changes a model in one file instead of in every app.
"""

import contextlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from fernway.budgets import data_dir, state_dir
from fernway.config import BACKUP_MODEL, CHEAP_MODEL, DEAD_PORT, DEFAULT_BASE_URL, DEFAULT_MODEL, PROXY_PORT

STARTUP_SECONDS = 120


def ensure_keys() -> dict[str, str]:
    """One gateway key per team, made once. They work only on this machine's proxy."""
    path = state_dir() / "keys.json"
    if path.exists():
        return json.loads(path.read_text())
    teams = json.loads((data_dir() / "teams.json").read_text())["teams"]
    keys = {team: f"sk-fernway-{team}-{secrets.token_hex(6)}" for team in teams}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, indent=1))
    return keys


def config(cheap_down: bool = False) -> dict:
    """The proxy's settings. The provider key is read from the environment, never written to the file."""
    base_url = os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL
    strong = os.environ.get("LLM_MODEL") or DEFAULT_MODEL

    def deployment(tier: str, model: str, **extra) -> dict:
        return {"model_name": tier, "litellm_params": {
            "model": f"openrouter/{model}", "api_key": "os.environ/LLM_API_KEY", "api_base": base_url, **extra}}

    cheap_base = f"http://127.0.0.1:{DEAD_PORT}/v1" if cheap_down else base_url
    return {
        "model_list": [
            deployment("strong", strong),
            # Reasoning off: the cheap tier is cheap only while it answers without thinking out loud.
            deployment("cheap", CHEAP_MODEL, api_base=cheap_base, extra_body={"reasoning": {"enabled": False}}),
            deployment("strong-backup", BACKUP_MODEL),
        ],
        "router_settings": {
            # Cheap fails over to strong, never the other way: an outage may cost more, never answer worse.
            "fallbacks": [{"cheap": ["strong"]}, {"strong": ["strong-backup"]}],
            "num_retries": 0,
            "timeout": 90,
        },
        "litellm_settings": {"callbacks": "fernway.hooks.gateway_hooks", "drop_params": True},
        "general_settings": {"custom_auth": "fernway.hooks.user_api_key_auth"},
    }


def write_config(cheap_down: bool = False) -> Path:
    path = state_dir() / "litellm.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    # JSON is valid YAML, so the file needs no YAML writer.
    path.write_text(json.dumps(config(cheap_down), indent=1))
    return path


def litellm_command() -> str:
    beside_python = Path(sys.executable).with_name("litellm")
    return str(beside_python) if beside_python.exists() else shutil.which("litellm") or "litellm"


def wait_until_up(port: int, process: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"The LiteLLM proxy exited while starting. See {log}.")
        with contextlib.suppress(OSError):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/liveliness", timeout=2) as answer:
                if answer.status == 200:
                    return
        time.sleep(0.5)
    process.terminate()
    raise SystemExit(f"The LiteLLM proxy did not start in {STARTUP_SECONDS} seconds. See {log}.")


@contextlib.contextmanager
def running_proxy(port: int = PROXY_PORT, cheap_down: bool = False, environment: dict | None = None):
    """Start `litellm --config`, wait for it to answer, and always stop it, even on Ctrl-C."""
    ensure_keys()
    config_path = write_config(cheap_down)
    log = state_dir() / "proxy.log"
    print(f"Starting the LiteLLM proxy on http://127.0.0.1:{port} with {config_path}...", flush=True)
    with log.open("w") as log_file:
        process = subprocess.Popen([litellm_command(), "--config", str(config_path), "--port", str(port),
                                    "--host", "127.0.0.1"], stdout=log_file, stderr=subprocess.STDOUT,
                                   env={**os.environ, **(environment or {})})
        try:
            wait_until_up(port, process, log)
            yield f"http://127.0.0.1:{port}"
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
