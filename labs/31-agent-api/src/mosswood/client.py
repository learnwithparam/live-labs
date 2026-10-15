"""How a caller reads the stream: the same steps the web app's page takes in JavaScript.

A browser's EventSource cannot send an Authorization header, so in-app assistants read Server-Sent
Events from a plain streaming POST instead, as the Vercel AI SDK does. This module does that, times the
first word, and can hang up part-way to show that the model call stops too.
"""

import json
import time
from dataclasses import dataclass, field

import httpx

from mosswood.config import AGENT_PORT, GATEWAY_PORT

GATEWAY = f"http://127.0.0.1:{GATEWAY_PORT}"
AGENT = f"http://127.0.0.1:{AGENT_PORT}"


@dataclass
class Reply:
    status: int
    request_id: str = ""
    retry_after: str = ""
    limit_scope: str = ""
    text: str = ""
    tools: list[dict] = field(default_factory=list)
    error: str = ""
    first_text_ms: float | None = None
    total_ms: float = 0.0
    tokens_read: int = 0
    hung_up: bool = False
    finish: dict = field(default_factory=dict)


def events(lines):
    """Turn SSE lines into (event, data) pairs. A blank line ends each event; `:` lines are keep-alive pings."""
    name, data = "message", []
    for line in lines:
        if not line:
            if data:
                yield name, json.loads("\n".join(data))
            name, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())


def stream_answer(question: str, token: str | None, hang_up_after: int | None = None, url: str = GATEWAY) -> Reply:
    """Ask through the gateway and read the answer as it streams. `hang_up_after` closes after that many tokens."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    started = time.perf_counter()
    with httpx.stream("POST", f"{url}/v1/chat", json={"question": question}, headers=headers, timeout=60) as response:
        reply = Reply(response.status_code, response.headers.get("x-request-id", ""),
                      response.headers.get("retry-after", ""), response.headers.get("x-limit-scope", ""))
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            reply.error = response.read().decode()[:200]
            reply.total_ms = (time.perf_counter() - started) * 1000
            return reply
        for name, data in events(response.iter_lines()):
            if name == "token":
                if reply.first_text_ms is None:
                    reply.first_text_ms = (time.perf_counter() - started) * 1000
                reply.text += data["text"]
                reply.tokens_read += 1
                if hang_up_after is not None and reply.tokens_read >= hang_up_after:
                    # Leaving the `with` block closes the connection, which is all a closed tab does.
                    reply.hung_up = True
                    break
            elif name == "tool":
                reply.tools.append(data)
            elif name == "error":
                reply.error = data["message"]
            elif name == "done":
                reply.finish = data
    reply.total_ms = (time.perf_counter() - started) * 1000
    return reply


def whole_answer(tenant_id: str, user_id: str, question: str) -> tuple[int, dict, float]:
    """The naive call: straight to the agent, no token, and one JSON body when it is all done."""
    started = time.perf_counter()
    response = httpx.post(f"{AGENT}/v1/answer", json={"tenant": tenant_id, "user": user_id, "question": question}, timeout=120)
    return response.status_code, response.json(), (time.perf_counter() - started) * 1000
