"""Run the real stand-in model and the real agent on spare ports, in threads, for one test."""

import socket
import threading
import time
from contextlib import contextmanager

import httpx
import uvicorn
from openai import AsyncOpenAI

from mosswood.agent import Ledger, create_app
from mosswood.config import STUB_MODEL
from mosswood.stub_model import Provider, create_stub


def spare_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def running(app):
    port = spare_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(5)


@contextmanager
def agent_over_stub(tmp_path, provider: Provider | None = None):
    """The agent with the stand-in model behind it, and the provider so a test can read its counts."""
    provider = provider or Provider()
    with running(create_stub(provider)) as stub_url:
        ledger = Ledger(tmp_path / "agent.jsonl")
        app = create_app(lambda: AsyncOpenAI(base_url=f"{stub_url}/v1", api_key="stand-in", max_retries=0,
                                             timeout=httpx.Timeout(10.0)), STUB_MODEL, ledger)
        with running(app) as agent_url:
            yield agent_url, provider, ledger


# What Envoy adds after it has checked the token. Only the gateway sets these in production.
GATEWAY_HEADERS = {"x-tenant-id": "pemberton", "x-user-id": "priya.raman", "x-request-id": "test-request"}
