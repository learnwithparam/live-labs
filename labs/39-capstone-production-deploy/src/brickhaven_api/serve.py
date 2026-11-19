"""`brickhaven-api serve`: the customer API on one port and the operator portal on another, in one process.

The portal is a separate listener bound to 127.0.0.1 so operator detail (models, costs, limits) is never
on the port a customer can reach.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from brickhaven_api.billing import OpenRouterBilling
from brickhaven_api.breaker import CircuitBreaker
from brickhaven_api.config import (API_PORT, BREAKER_COOLDOWN_S, BREAKER_FAILURES, MAX_BODY_BYTES, OUT, PORTAL_PORT,
                                   llm_settings, model_slots)
from brickhaven_api.keys import ClientKeys
from brickhaven_api.ledger import Ledger, reconcile
from brickhaven_api.limiter import RateLimiter
from brickhaven_api.provider import FaultInjectingProvider, OpenRouterProvider
from brickhaven_api.router import Router
from brickhaven_api.service import App

PAGE = Path(__file__).with_name("page.html")


def build_app(naive: bool = False, break_primary: bool = False) -> App:
    base_url, api_key = llm_settings()
    primary, fallback = model_slots()
    provider = OpenRouterProvider(base_url, api_key)
    if break_primary:
        provider = FaultInjectingProvider(provider, {primary})
    breakers = {model: CircuitBreaker(BREAKER_FAILURES, BREAKER_COOLDOWN_S) for model in (primary, fallback)}
    mode = "naive" if naive else "hardened"
    return App(provider, Router(provider, primary, fallback, breakers), ClientKeys(), RateLimiter(), Ledger(OUT / f"ledger-{mode}.jsonl"),
               hardened=not naive, billing=OpenRouterBilling(base_url, api_key), changes_path=OUT / "limit_changes.jsonl")


class BaseHandler(BaseHTTPRequestHandler):
    app: App

    def reply(self, status: int, body: bytes, kind: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def json_reply(self, status: int, payload: dict, headers: dict | None = None) -> None:
        self.reply(status, json.dumps(payload).encode(), "application/json", headers)

    def read_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            return None
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return None
        return body if isinstance(body, dict) else None

    def log_message(self, *_: object) -> None:
        pass


class ApiHandler(BaseHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self.json_reply(200, {"mode": "hardened" if self.app.hardened else "naive"})
        else:
            self.json_reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        body = self.read_body()
        if self.path != "/v1/work-orders":
            return self.json_reply(404, {"error": "not found"})
        if body is None:
            return self.json_reply(400, {"error": "send a small JSON object"})
        reply = self.app.handle_work_order(self.headers.get("Authorization", ""), body)
        self.json_reply(reply.status, reply.payload, reply.headers)


class PortalHandler(BaseHandler):
    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/summary":
            self.json_reply(200, self.app.summary())
        else:
            self.reply(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        # A page on another site must not be able to raise a limit: only this page may.
        origin = self.headers.get("Origin", "")
        if origin and urlsplit(origin).hostname not in ("127.0.0.1", "localhost"):
            return self.json_reply(403, {"error": "local only"})
        body = self.read_body()
        if body is None:
            return self.json_reply(400, {"error": "send a small JSON object"})
        if self.path == "/api/limit":
            try:
                self.app.raise_limit(str(body.get("operator", "")), str(body.get("client", "")), int(body.get("per_minute", 0)), str(body.get("reason", "")))
            except (ValueError, TypeError) as error:
                return self.json_reply(400, {"error": str(error)})
            self.json_reply(200, self.app.summary())
        elif self.path == "/api/reconcile" and self.app.billing:
            result = reconcile(self.app.ledger.rows[-20:], self.app.billing.lookup)
            self.json_reply(200, {"checked": result.checked, "missing": len(result.missing), "ledger_total": result.ledger_total,
                                  "billing_total": result.billing_total, "gap_share": result.gap_share, "within_tolerance": result.within_tolerance})
        else:
            self.json_reply(404, {"error": "not found"})


class BurstSafeServer(ThreadingHTTPServer):
    # The default listen backlog is 5, so a burst of a dozen callers gets connections refused before
    # the rate limiter ever sees them. Those refusals would be counted as service errors.
    request_queue_size = 128


def start_servers(app: App, api_port: int, portal_port: int) -> tuple[ThreadingHTTPServer, ThreadingHTTPServer]:
    """Bind both listeners and serve each in its own thread. Port 0 picks a free port, which tests use."""
    api = BurstSafeServer(("127.0.0.1", api_port), type("BoundApiHandler", (ApiHandler,), {"app": app}))
    portal = BurstSafeServer(("127.0.0.1", portal_port), type("BoundPortalHandler", (PortalHandler,), {"app": app}))
    for server in (api, portal):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return api, portal


def run(naive: bool, break_primary: bool, api_port: int) -> None:
    app = build_app(naive, break_primary)
    start_servers(app, api_port, api_port + 1)
    print(f"{'naive' if naive else 'hardened'} API on http://127.0.0.1:{api_port}, portal on http://127.0.0.1:{api_port + 1}", flush=True)
    threading.Event().wait()
