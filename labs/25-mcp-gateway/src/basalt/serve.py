"""`gateway serve`: the platform team's MCP server approval console, on top of the running gateway."""

import asyncio
import json
import signal
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from basalt import catalogue, gateway
from basalt.config import CONSOLE_PORT, DATA, HOST, OUT
from basalt.guard import DriftAlerts
from basalt.review import REVIEW
from basalt.stack import running, try_as

PAGE = Path(__file__).with_name("page.html")
ADD_REQUESTS = DATA / "add-requests.json"
DECISIONS = OUT / "add-decisions.json"
REVIEWER = "priya.shah@basaltpay.com"
TEAMS = ["support", "support-contractors", "risk", "engineering"]
# What a test call sends, so the console can exercise the gateway with no model and no key.
SAMPLE_CALLS = {
    "payments_find_payment": {"payment_id": "PAY-88412"},
    "payments_refund_payment": {"payment_id": "PAY-88411", "amount": 1.0, "reason": "duplicate"},
    "helpdesk_find_ticket": {"ticket_id": "HD-5521"},
    "deploys_service_status": {"service": "payouts"},
    "merchants_lookup_merchant": {"name": "Copperleaf Bakery"},
    "merchants_sync_record": {"record": {"name": "Copperleaf Bakery"}},
    "snippets_save_snippet": {"title": "HD-5530 summary", "text": "Payout hold after a bank account change."},
}


class Console:
    """The whole stack on a background event loop, kept up while the console is open.

    The vendor's 2.1 update is shipped at start, as it was before the review, so the console shows
    the same world the review scored.
    """

    def __init__(self, review_file: Path = REVIEW):
        self.review_file, self.lock = review_file, threading.Lock()
        self.alerts, self.states, self.inspected = DriftAlerts(), {}, {}
        self.loop = asyncio.new_event_loop()
        started = threading.Event()

        async def keep_running():
            self.stopping = asyncio.Event()
            async with running() as stack:
                self.stack = stack
                stack.fleet.merchants.ship_update()
                await self.check_pins()
                started.set()
                await self.stopping.wait()

        self.thread = threading.Thread(target=self.loop.run_until_complete, args=(keep_running(),), daemon=True)
        self.thread.start()
        if not started.wait(30):
            raise SystemExit("The gateway stack did not start. Is a port from 8250 to 8257 in use?")

    def run(self, coroutine, timeout: float = 60):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.stopping.set)
        self.thread.join(15)

    async def check_pins(self) -> dict:
        """Compare every approved server's live tools to their pins, and raise an alert for each change."""
        states = {}
        for server in catalogue.load().values():
            for row in catalogue.compare(server, await catalogue.live_definitions(server.url)):
                states[(server.name, row["tool"])] = row["state"]
                if row["state"] == "changed":
                    self.alerts.raise_for(row)
        self.states = states
        return states

    def state(self) -> dict:
        servers = catalogue.load()
        review = json.loads(self.review_file.read_text()) if self.review_file.exists() else None
        decisions = json.loads(DECISIONS.read_text()) if DECISIONS.exists() else {}
        requests = [{**each, "decision": decisions.get(each["id"])}
                    for each in json.loads(ADD_REQUESTS.read_text())["requests"]]
        return {
            "servers": [{"name": server.name, "url": server.url, "owner": server.owner, "owning_team": server.owning_team,
                         "approved_by": server.approved_by, "approved_on": server.approved_on,
                         "tools": [{"name": tool.name, "teams": tool.teams, "pin": tool.sha256,
                                    "state": self.states.get((server.name, tool.name), "unchecked")}
                                   for tool in server.tools.values()],
                         "unlisted": [tool for (name, tool), state in self.states.items()
                                      if name == server.name and state == "not on the list"]}
                        for server in servers.values()],
            "alerts": self.alerts.read(),
            "log": gateway.call_log()[-40:],
            "requests": requests,
            "review": {mode: review[mode]["score"] for mode in ("direct", "gateway")} if review else None,
            "review_rows": [{key: row[key] for key in ("id", "person", "team", "ask", "got_through")}
                            for row in review["direct"]["rows"]] if review else [],
            "model": review["model"] if review else None,
            "people": catalogue.staff(), "teams": TEAMS, "samples": list(SAMPLE_CALLS), "reviewer": REVIEWER,
        }

    def test_call(self, person: str, tool: str) -> dict:
        reason = self.run(try_as(person, tool, SAMPLE_CALLS[tool]))
        return {"ran": not reason, "reason": reason}

    def decide_drift(self, server: str, tool: str, current: str, decision: str) -> None:
        """A reviewer's call on a changed tool. Re-approving pins the new definition; it is never automatic."""
        with self.lock:
            alerts = self.alerts.read()
            for alert in alerts:
                if (alert["server"], alert["tool"], alert["current"]) == (server, tool, current):
                    alert["decision"] = {"choice": decision, "by": REVIEWER, "on": date.today().isoformat()}
            self.alerts.path.write_text(json.dumps(alerts, indent=1))
            if decision == "re-approve":
                servers = catalogue.load()
                servers[server].tools[tool].sha256 = current
                catalogue.save(servers)
        self.run(self.check_pins())

    def inspect(self, request_id: str) -> list[dict]:
        """The requested server's tools exactly as a model would read them, with the hash that would be pinned."""
        wanted = self.add_request(request_id)
        live = self.run(catalogue.live_definitions(wanted["url"]))
        tools = [{"name": tool["name"], "description": tool.get("description") or "", "hash": catalogue.tool_hash(tool),
                  "schema": tool.get("inputSchema") or {}} for tool in live]
        self.inspected[request_id] = {tool["name"]: tool["hash"] for tool in tools}
        return tools

    def decide_request(self, request_id: str, decision: str, teams: dict[str, list[str]], note: str) -> dict:
        """Approve with a team list per tool, or reject. Approval pins what the reviewer inspected, no newer text."""
        wanted = self.add_request(request_id)
        if decision == "approve":
            live = self.run(catalogue.live_definitions(wanted["url"]))
            inspected = self.inspected.get(request_id, {})
            if not inspected or any(inspected.get(tool["name"]) != catalogue.tool_hash(tool) for tool in live):
                return {"error": "The tools changed since you inspected them. Inspect again before approving."}
            chosen = [{**tool, "teams": [team for team in teams.get(tool["name"], []) if team in TEAMS]} for tool in live]
            if not any(tool["teams"] for tool in chosen):
                return {"error": "Choose at least one team for one tool, or reject the request."}
            with self.lock:
                servers = catalogue.approve(catalogue.load(), wanted["server"], wanted["url"], wanted["requested_by"],
                                            wanted["team"], REVIEWER, chosen)
                catalogue.save(servers)
                # agentgateway reloads its config file on change, so the new route is live without a restart.
                gateway.write_config(servers)
        with self.lock:
            decisions = json.loads(DECISIONS.read_text()) if DECISIONS.exists() else {}
            decisions[request_id] = {"choice": decision, "by": REVIEWER, "on": date.today().isoformat(), "note": note[:400]}
            DECISIONS.parent.mkdir(exist_ok=True)
            DECISIONS.write_text(json.dumps(decisions, indent=1))
        self.run(self.check_pins())
        return {"ok": True}

    def add_request(self, request_id: str) -> dict:
        for each in json.loads(ADD_REQUESTS.read_text())["requests"]:
            if each["id"] == request_id:
                return each
        raise KeyError(request_id)


def handler(console: Console) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, data, status: int = 200) -> None:
            self.send(status, json.dumps(data).encode())

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: a foreign page could otherwise approve a server."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            path = urlsplit(self.path).path
            if path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self.send_json(console.state())
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except json.JSONDecodeError:
                return self.send(400, b"not json", "text/plain")
            path = urlsplit(self.path).path
            try:
                if path == "/api/test-call" and body.get("tool") in SAMPLE_CALLS and body.get("person") in catalogue.staff():
                    return self.send_json(console.test_call(body["person"], body["tool"]))
                if path == "/api/pins/check":
                    console.run(console.check_pins())
                    return self.send_json({"ok": True})
                if path == "/api/drift" and body.get("decision") in ("keep-blocked", "re-approve"):
                    console.decide_drift(str(body["server"]), str(body["tool"]), str(body["current"]), body["decision"])
                    return self.send_json({"ok": True})
                if path == "/api/requests/inspect":
                    return self.send_json({"tools": console.inspect(str(body.get("id")))})
                if path == "/api/requests/decide" and body.get("decision") in ("approve", "reject"):
                    teams = body.get("teams") if isinstance(body.get("teams"), dict) else {}
                    result = console.decide_request(str(body.get("id")), body["decision"], teams, str(body.get("note") or ""))
                    return self.send_json(result, 409 if "error" in result else 200)
            except KeyError:
                return self.send(404, b"no such request or server", "text/plain")
            self.send(400, b"unknown action", "text/plain")

    return Handler


def serve(console: Console, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), handler(console))


def stop_on_terminate(signal_number, frame) -> None:
    # A console started in the background gets SIGTERM, not Ctrl-C; both must stop agentgateway too.
    raise KeyboardInterrupt


def main(port: int | None = None) -> int:
    signal.signal(signal.SIGTERM, stop_on_terminate)
    console = Console()
    server = serve(console, port or CONSOLE_PORT)
    print(f"Basalt Pay MCP console on http://{HOST}:{port or CONSOLE_PORT}. Gateway on :8250. Ctrl-C stops both.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        console.stop()
    return 0
