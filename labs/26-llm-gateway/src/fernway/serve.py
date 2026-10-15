"""`gateway serve`: Fernway's LLM gateway admin console, local only, on the gateway's own books."""

import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from fernway.budgets import Budgets
from fernway.evaluate import compare, load
from fernway.quality import items

PAGE = Path(__file__).with_name("page.html")
ADMIN = "platform admin"
RECENT_ROWS = 14


class Console:
    """What the platform admin sees: each team's spend, the metric, and the calls waiting on a decision."""

    def __init__(self, budgets: Callable | None = None):
        self.make_budgets = budgets or Budgets
        self.asks = {item["id"]: item for item in items()}

    def teams(self, budgets: Budgets, rows: list[dict]) -> list[dict]:
        decisions = budgets.decisions()
        teams = []
        for team, info in budgets.teams.items():
            mine = [row for row in rows if row.get("team") == team]
            spent, budget = budgets.spent(team), budgets.budget(team)
            teams.append({
                "team": team, "name": info["name"], "calls_for": info["calls_for"], "budget": budget,
                "set_budget": info["budget"], "spent": spent, "invoice": budgets.invoice["spend"].get(team, 0.0),
                "calls": sum(row.get("status") == "ok" for row in mine),
                "refused": sum(row.get("status") == "refused" for row in mine),
                "over": spent >= budget, "raised": decisions["budgets"].get(team),
                "held": decisions.get("held", {}).get(team)})
        return teams

    def flagged(self, budgets: Budgets, routed: dict | None) -> list[dict]:
        if not routed:
            return []
        labels = budgets.decisions()["routes"]
        return [{"call_id": result["call_id"], "item": result["id"], "team": result["team"],
                 "ask": self.asks.get(result["id"], {}).get("ask", ""), "tier": result["tier"],
                 "reason": result["reason"], "passed": result["passed"], "cost": result["cost"],
                 "decision": labels.get(result["call_id"])}
                for result in routed["results"] if result["flagged"] and result.get("call_id")]

    def state(self) -> dict:
        budgets = self.make_budgets()
        rows = budgets.ledger.rows()
        naive, routed, cheap = load("strong"), load("routed"), load("cheap")
        metric = compare(naive, routed) if naive and routed else None
        side_by_side = []
        if naive and routed:
            by_id = {result["id"]: result for result in routed["results"]}
            for result in naive["results"]:
                other = by_id.get(result["id"], {})
                side_by_side.append({"id": result["id"], "team": result["team"], "difficulty": result["difficulty"],
                                     "strong": {"passed": result["passed"], "cost": result["cost"]},
                                     "routed": {"passed": other.get("passed"), "cost": other.get("cost"),
                                                "tier": other.get("tier"), "reason": other.get("reason"),
                                                "flagged": other.get("flagged"), "wrong": other.get("wrong", [])}})
        summaries = {mode: {key: run[key] for key in ("passed", "requests", "cost", "cost_per_request", "tiers",
                                                       "flagged", "all_billed")}
                     for mode, run in (("strong", naive), ("routed", routed), ("cheap", cheap)) if run}
        return {"metric": metric, "runs": summaries, "items": side_by_side,
                "teams": self.teams(budgets, rows), "flagged": self.flagged(budgets, routed),
                "recent": [row for row in rows if row.get("purpose") == "answer"][-RECENT_ROWS:][::-1]}

    def raise_budget(self, team: str, new_budget: float) -> dict:
        self.make_budgets().raise_budget(team, new_budget, ADMIN)
        return self.state()

    def hold(self, team: str) -> dict:
        self.make_budgets().keep_refused(team, ADMIN)
        return self.state()

    def label(self, call_id: str, tier: str) -> dict:
        self.make_budgets().label_route(call_id, tier, ADMIN)
        return self.state()


def handler(console: Console) -> type[BaseHTTPRequestHandler]:
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
            """Only this machine's own page may call: the console raises budgets and spends real money."""
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
                return self.send(400, b"a JSON body", "text/plain")
            path = urlsplit(self.path).path
            teams = Budgets().teams
            team, budget = body.get("team"), body.get("budget")
            if path == "/api/budget":
                if team not in teams or not isinstance(budget, (int, float)) or not 0 < budget <= 10 * teams[team]["budget"]:
                    return self.send(400, b"a known team and a budget above zero, at most ten times the set one",
                                     "text/plain")
                return self.send_json(console.raise_budget(team, float(budget)))
            if path == "/api/hold":
                if team not in teams:
                    return self.send(400, b"a known team", "text/plain")
                return self.send_json(console.hold(team))
            if path == "/api/route":
                call_id, tier = body.get("call_id"), body.get("tier")
                known = {row["call_id"] for row in console.state()["flagged"]}
                if call_id not in known or tier not in ("cheap", "strong"):
                    return self.send(400, b"a flagged call and a tier of cheap or strong", "text/plain")
                return self.send_json(console.label(call_id, tier))
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(console: Console, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(console))


def main(port: int) -> int:
    server = serve(Console(), port)
    print(f"Fernway LLM gateway admin console on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
