"""`govern serve`: Larkspur's audit request desk, local only, over the inventory and the audit log."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from larkspur import inventory, naive, signoff
from larkspur.audit import Desk, answer_all, requests_of
from larkspur.auditlog import AuditLog
from larkspur.config import ANSWER_PACK, APP_LOGS, AUDIT_DB, DATA, DECISIONS, OUT

PAGE = Path(__file__).with_name("page.html")
OFFICER = "Ines Duarte, Head of Compliance"


class EvidenceRoom:
    """Reads the inventory, the log and the officer's files fresh on every request, so the page is never stale."""

    def __init__(self, key: bytes, data_dir: Path = DATA, out_dir: Path = OUT):
        self.key, self.data_dir = key, data_dir
        self.logs = {"live": out_dir / AUDIT_DB.name, "tampered": out_dir / "audit-tampered.sqlite"}
        self.app_logs = out_dir / APP_LOGS.name
        self.decisions_file, self.pack_file = out_dir / DECISIONS.name, out_dir / ANSWER_PACK.name

    def answers(self, copy: str) -> tuple[dict, dict, dict[str, inventory.System]]:
        systems = inventory.load(self.data_dir)
        pack = signoff.read_json(self.pack_file, {})
        log = AuditLog(self.logs[copy])
        try:
            requests = requests_of(self.data_dir)
            governed = answer_all(Desk(systems, log, self.key, pack.get("anchor")), requests)
        finally:
            log.close()
        return governed, naive.answer_all(self.app_logs, requests), systems

    def state(self, copy: str) -> dict:
        governed, grepped, systems = self.answers(copy)
        findings, decisions = inventory.findings(systems), signoff.read_json(self.decisions_file, {})
        pack = signoff.read_json(self.pack_file, None)
        return {"officer": OFFICER, "copy": copy, "copies": [name for name, path in self.logs.items() if path.exists()],
                "metric": {"answered": governed["answered"], "total": governed["total"],
                           "median_ms": round(governed["median_seconds"] * 1000, 1),
                           "naive_answered": grepped["answered"], "naive_steps": grepped["steps"],
                           "naive_lines": grepped["lines_read"]},
                "requests": [{"id": row["id"], "asked": row["asked"],
                              "ok": row["grade"]["correct"] and row["grade"]["complete"], "naive_ok": naive_row["complete"]}
                             for row, naive_row in zip(governed["rows"], grepped["rows"])],
                "chain": governed["chain"], "findings": [{**finding, "decision": decisions.get(finding["system"])}
                                                         for finding in findings],
                "blockers": signoff.blockers(governed, findings, decisions),
                "pack": pack and {"signed_by": pack["signed_by"], "signed_at": pack["signed_at"], "anchor": pack["anchor"]},
                "systems": len(systems)}

    def request(self, request_id: str, copy: str) -> dict | None:
        governed, grepped, _ = self.answers(copy)
        for row, naive_row in zip(governed["rows"], grepped["rows"]):
            if row["id"] == request_id:
                return {"governed": row, "naive": naive_row, "chain": governed["chain"]}
        return None

    def decide(self, system: str, decision: str) -> dict:
        signoff.decide(self.decisions_file, system, decision, OFFICER, inventory.findings(inventory.load(self.data_dir)))
        return self.state("live")

    def sign(self) -> dict:
        governed, _, systems = self.answers("live")
        signoff.sign(self.pack_file, governed, inventory.findings(systems), signoff.read_json(self.decisions_file, {}), OFFICER)
        return self.state("live")


def handler(room: EvidenceRoom) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: the log holds what customers were told."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def copy(self) -> str:
            chosen = parse_qs(urlsplit(self.path).query).get("log", ["live"])[0]
            return chosen if chosen in room.logs and room.logs[chosen].exists() else "live"

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            path = urlsplit(self.path).path
            if path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if not room.logs["live"].exists():
                return self.send(409, json.dumps({"error": "No audit log yet. Run `uv run govern replay`."}).encode())
            if path == "/api/state":
                return self.send(200, json.dumps(room.state(self.copy())).encode())
            if path.startswith("/api/request/"):
                found = room.request(path.removeprefix("/api/request/"), self.copy())
                if found:
                    return self.send(200, json.dumps(found).encode())
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            path = urlsplit(self.path).path
            try:
                if path == "/api/decide":
                    return self.send(200, json.dumps(room.decide(str(body.get("system")), str(body.get("decision")))).encode())
                if path == "/api/signoff":
                    return self.send(200, json.dumps(room.sign()).encode())
            # A refused decision or signature comes back as words the officer can act on.
            except ValueError as error:
                return self.send(409, json.dumps({"error": str(error)}).encode())
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(room: EvidenceRoom, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(room))


def main(port: int) -> int:
    from larkspur.config import pseudonym_key

    server = serve(EvidenceRoom(pseudonym_key()), port)
    print(f"Larkspur audit request desk on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
