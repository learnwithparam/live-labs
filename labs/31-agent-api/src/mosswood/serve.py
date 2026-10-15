"""`assistant serve`: the Mosswood web app with its assistant, and the gateway operator view. Local only."""

import json
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from mosswood import gateway, gatewaylog
from mosswood.config import GATEWAY_PORT, OUT, WINDOW_SECONDS
from mosswood.tokens import issue
from mosswood.workspace import workspaces

PAGE = Path(__file__).with_name("page.html")
LOAD_SUMMARY = OUT / "load" / "gateway.json"
RECENT_ROWS = 12
OPERATOR = "Mosswood operator"


class Operator:
    """What the page reads and the one decision it can make, with the gateway's reload passed in for tests."""

    def __init__(self, reload: Callable[[], str] | None = None, log: Path = gateway.ACCESS_LOG):
        self.reload = reload or (lambda: gateway.reload() if gateway.running() else "the gateway is not running")
        self.log = log

    def since(self) -> float:
        if LOAD_SUMMARY.exists():
            return json.loads(LOAD_SUMMARY.read_text())["started"]
        return 0.0

    def state(self) -> dict:
        started = self.since()
        logged = gatewaylog.rows(self.log, since=started)
        limits = gateway.limits()
        summary = gatewaylog.summarise(logged, limits)
        return {"since": started, "window_seconds": WINDOW_SECONDS, "limits": limits, "decisions": gateway.decisions(),
                "summary": summary, "recent": logged[-RECENT_ROWS:][::-1], "gateway_running": gateway.running()}

    def decide(self, tenant_id: str, keep: bool, new_limit: int | None, reason: str) -> dict:
        gateway.decide(tenant_id, keep=keep, new_limit=new_limit, operator=OPERATOR, reason=reason)
        problem = self.reload()
        return {**self.state(), "reload_problem": problem}


def handler(operator: Operator) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, payload: dict) -> None:
            self.send(200, json.dumps(payload).encode(), "application/json")

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: it signs tokens and changes a tenant's limit."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            path = urlsplit(self.path).path
            if path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/workspaces":
                return self.send_json({**workspaces(), "gateway_port": GATEWAY_PORT})
            if path == "/api/gateway":
                return self.send_json(operator.state())
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except json.JSONDecodeError:
                return self.send(400, b"a JSON body", "text/plain")
            path = urlsplit(self.path).path
            tenants = workspaces()["tenants"]
            tenant_id = body.get("tenant")
            if path == "/api/token":
                # The web app's own login would do this; the page asks for a fresh five-minute token each time.
                if tenant_id not in tenants or body.get("user") not in tenants[tenant_id]["users"]:
                    return self.send(400, b"a known tenant and user", "text/plain")
                return self.send_json({"token": issue(tenant_id, body["user"]), "issued_at": time.time()})
            if path == "/api/limit":
                keep, new_limit = bool(body.get("keep")), body.get("limit")
                if tenant_id not in tenants:
                    return self.send(400, b"a known tenant", "text/plain")
                if not keep and (not isinstance(new_limit, int) or not 1 <= new_limit <= 200):
                    return self.send(400, b"keep, or a new limit from 1 to 200", "text/plain")
                return self.send_json(operator.decide(tenant_id, keep, None if keep else new_limit, str(body.get("reason", ""))[:300]))
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(operator: Operator, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(operator))


def main(port: int) -> int:
    server = serve(Operator(), port)
    print(f"Mosswood on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
