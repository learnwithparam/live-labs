"""The egress proxy: the one door out of the sandbox network, and it only opens onto the model API.

Sandboxes sit on a Docker network with no route to the internet. This proxy is the only other container
on it, and it forwards exactly one request shape, a chat completion, to one upstream. Three rules live
here rather than in the agent, because the agent is the thing being contained:

- The real API key is held by the proxy. A sandbox gets a session token that is worthless anywhere else.
- The model is pinned, so a session cannot switch itself to an expensive one.
- Each session has a dollar budget. Once it is spent, the proxy answers 402 and the session stops.

Standard library only: it runs from the sandbox image as `python /opt/fleet/egress.py`.
"""

import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The request fields the agent sends. Anything else, such as OpenRouter's `models`, `plugins` or `n`, could
# switch the model, reach the web or multiply the bill, so it is dropped rather than forwarded.
FORWARDED_FIELDS = ("messages", "tools", "tool_choice")
MESSAGE_KEYS = {"role", "content", "tool_calls", "tool_call_id", "name"}


def shape_problem(request: dict) -> str | None:
    """Why a request is not the plain text and function tools the agent sends, or None.

    Inside the forwarded fields a message can still carry an image URL the provider fetches, and a tool can
    be a provider server tool such as web search. Both reach past the allow list, so both are refused.
    """
    messages, tools = request.get("messages"), request.get("tools", [])
    if not isinstance(messages, list) or not isinstance(tools, list):
        return "messages and tools must be lists"
    for message in messages:
        if not isinstance(message, dict) or not set(message) <= MESSAGE_KEYS:
            return "a message has a field the agent never sends"
        if message.get("role") not in {"system", "user", "assistant", "tool"}:
            return "a message has an unknown role"
        if not isinstance(message.get("content"), (str, type(None))):
            return "a message is not plain text"
    if any(not isinstance(tool, dict) or tool.get("type") != "function" for tool in tools):
        return "only function tools are allowed"
    choice = request.get("tool_choice", "auto")
    if choice not in ("auto", "none", "required") and not (isinstance(choice, dict) and choice.get("type") == "function"):
        return "tool_choice must name a function tool"
    return None


class Ledger:
    """Budget and spend per session token, plus every refused request, for the orchestrator to read."""

    def __init__(self, prices: tuple[float, float, float]):
        self.prices, self.sessions, self.lock = prices, {}, threading.Lock()
        # One call in flight per session: the budget is checked before a call and charged after it, so
        # concurrent calls would all pass the check on the same unspent balance.
        self.turns: dict[str, threading.Lock] = {}

    def open(self, token: str, session: str, budget: float) -> None:
        with self.lock:
            self.sessions[token] = {"session": session, "budget": budget, "spent": 0.0, "calls": 0, "refused": []}
            self.turns[token] = threading.Lock()

    def cost_of(self, usage: dict) -> float:
        """The bill when the upstream reports it, else list prices. A budget needs a number either way."""
        if usage.get("cost") is not None:
            return float(usage["cost"])
        price_in, price_cached, price_out = self.prices
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        return ((usage.get("prompt_tokens", 0) - cached) * price_in + cached * price_cached
                + usage.get("completion_tokens", 0) * price_out) / 1e6

    def refuse(self, token: str | None, reason: str) -> None:
        with self.lock:
            if token in self.sessions:
                self.sessions[token]["refused"].append(reason)

    def report(self) -> dict:
        with self.lock:
            return {entry["session"]: {key: value for key, value in entry.items() if key != "session"}
                    for entry in self.sessions.values()}


def handler(ledger: Ledger, upstream: str, upstream_key: str, model: str, admin_token: str,
            send_upstream=None) -> type[BaseHTTPRequestHandler]:
    def forward(body: bytes) -> tuple[int, bytes]:
        request = urllib.request.Request(f"{upstream}/chat/completions", data=body, method="POST", headers={
            "Authorization": f"Bearer {upstream_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    send = send_upstream or forward

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, payload: dict | bytes) -> None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

        def session_token(self) -> str | None:
            header = self.headers.get("Authorization", "")
            return header.removeprefix("Bearer ").strip() or None

        def do_GET(self) -> None:
            if self.path == "/admin/ledger" and self.headers.get("X-Admin-Token") == admin_token:
                return self.reply(200, ledger.report())
            ledger.refuse(self.session_token(), f"GET {self.path} is not on the allow list")
            self.reply(403, {"error": {"message": "egress denied: only chat completions to the model API"}})

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path == "/admin/sessions":
                if self.headers.get("X-Admin-Token") != admin_token:
                    return self.reply(403, {"error": {"message": "not the orchestrator"}})
                opened = json.loads(body)
                ledger.open(opened["token"], opened["session"], float(opened["budget"]))
                return self.reply(200, {"ok": True})
            token = self.session_token()
            entry = ledger.sessions.get(token)
            if self.path != "/v1/chat/completions":
                ledger.refuse(token, f"POST {self.path} is not on the allow list")
                return self.reply(403, {"error": {"message": "egress denied: only chat completions to the model API"}})
            if entry is None:
                return self.reply(401, {"error": {"message": "unknown session token"}})
            request = json.loads(body)
            if problem := shape_problem(request):
                ledger.refuse(token, f"request refused: {problem}")
                return self.reply(403, {"error": {"message": f"egress denied: {problem}"}})
            with ledger.turns[token]:
                if entry["spent"] >= entry["budget"]:
                    ledger.refuse(token, f"cost budget ${entry['budget']:g} spent")
                    return self.reply(402, {"error": {"message": f"session cost budget ${entry['budget']:g} is spent"}})
                # The proxy, not the agent, decides the model and asks for the bill on every call.
                forwarded = {key: request[key] for key in FORWARDED_FIELDS if key in request}
                forwarded.update(model=model, usage={"include": True})
                status, answer = send(json.dumps(forwarded).encode())
                if status == 200:
                    usage = json.loads(answer).get("usage") or {}
                    with ledger.lock:
                        entry["spent"] += ledger.cost_of(usage)
                        entry["calls"] += 1
            self.reply(status, answer)

    return Handler


def main() -> None:
    prices = tuple(float(price) for price in os.environ["MODEL_PRICES"].split(","))
    port = int(os.environ.get("PROXY_PORT", "8321"))
    server = ThreadingHTTPServer(("0.0.0.0", port), handler(
        Ledger(prices), os.environ["UPSTREAM_URL"].rstrip("/"), os.environ["UPSTREAM_KEY"], os.environ["MODEL"],
        os.environ["ADMIN_TOKEN"]))
    print(f"egress proxy on :{port}, forwarding chat completions for {os.environ['MODEL']}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
