"""A fake GPU endpoint in this process: the stand-in's routes and key rule on a free port, no GPU, no key.

It can hang on one item and ramble on another, so a timeout and a failed validation are both tested
against a real socket rather than a mocked function.
"""

import threading
from http.server import ThreadingHTTPServer

from corvane_gpu.stand_in import handler


class FakeEndpoint:
    def __init__(self, api_key: str = "test-key", hang_on: str = "", hang_seconds: float = 5.0, ramble_on: str = ""):
        self.api_key = api_key
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(api_key, 0.0, hang_on, hang_seconds, ramble_on))
        self.server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def __enter__(self) -> "FakeEndpoint":
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *details) -> None:
        self.server.shutdown()
        self.server.server_close()


def outcome(item_id: str, status: str = "ok", latency: float = 0.5, label: str | None = "meals",
            started_at: float = 0.0, tokens: tuple[int, int] = (100, 2)) -> dict:
    """One burst result shaped like Outcome.as_dict(), for tests of the maths."""
    return {"id": item_id, "status": status, "answer": label or "", "label": label, "started_at": started_at,
            "latency_seconds": latency, "prompt_tokens": tokens[0], "completion_tokens": tokens[1], "cost": None,
            "error": "" if status == "ok" else "no answer in 3 s"}
