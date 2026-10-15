"""`tracker serve`: Larch's web app, with the assistant connecting to either MCP server and asking for consent on the page."""

import asyncio
import copy
import json
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from larch.assistant import CLIENT_NAME, Consent, MemoryTokens, ask, signed_in
from larch.config import PAGE_PORT, RESOURCE, SHARED_KEY_URL
from larch.review import SERVERS, consents, outside_grant, requests
from larch.stack import running
from larch.tracker import NEEDS, Credential, Refused

PAGE = Path(__file__).with_name("page.html")
REVIEW = Path("out/review.json")
WEB_APP = "Larch web app"


class Larch:
    """One workspace and its three servers on a background event loop, kept up while the page is open.

    Runs happen one at a time, because there is one assistant and one consent screen. When the
    assistant has no token for a person, the run waits until the person answers the consent sheet.
    """

    def __init__(self, models, review_file: Path = REVIEW):
        self.make_models, self.models_cache, self.review_file = models, None, review_file
        self.runs, self.current, self.lock = {}, None, threading.Lock()
        self.tokens: dict[str, MemoryTokens] = {}
        self.consent: dict | None = None
        self.loop = asyncio.new_event_loop()
        started, self.stopping = threading.Event(), None

        async def keep_running():
            self.stopping = asyncio.Event()
            async with running() as stack:
                self.stack = stack
                started.set()
                await self.stopping.wait()

        self.thread = threading.Thread(target=self.loop.run_until_complete, args=(keep_running(),), daemon=True)
        self.thread.start()
        started.wait(20)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.stopping.set)
        self.thread.join(10)

    def models(self) -> tuple:
        with self.lock:
            self.models_cache = self.models_cache or self.make_models()
            return self.models_cache

    def grants(self) -> list[dict]:
        workspace = self.stack.workspace
        return [{"person": person, "name": workspace.name_of(person), "client": client, "scopes": scopes}
                for (person, client), scopes in self.stack.authority.grants.items()]

    def state(self) -> dict:
        workspace = self.stack.workspace
        review = json.loads(self.review_file.read_text()) if self.review_file.exists() else None
        return {"workspace": workspace.data, "audit": workspace.audit, "grants": self.grants(), "names": SERVERS,
                "people": [{**user, "can_grant": workspace.role_scopes(user["id"])} for user in workspace.data["users"]],
                "requests": requests(), "consent": self.consent and {key: value for key, value in self.consent.items() if key != "answer"},
                "running": self.current, "review": {server: result["score"] for server, result in review.items()} if review else None}

    def granted(self, server: str, person: str) -> list[str]:
        """The yardstick for "outside the grant": what the authorization server recorded, or what they would tick."""
        if server == "oauth":
            return next((grant["scopes"] for grant in self.grants() if grant["person"] == person), [])
        return consents()[person]

    def start(self, server: str, person: str, message: str, request_id: str | None) -> str | None:
        with self.lock:
            if self.current:
                return None
            run_id = uuid.uuid4().hex[:8]
            self.current = run_id
        run = {"steps": [], "done": False, "server": server, "person": person, "message": message, "request": request_id}
        self.runs[run_id] = run
        asyncio.run_coroutine_threadsafe(self.work(run_id, run), self.loop)
        return run_id

    async def work(self, run_id: str, run: dict) -> None:
        workspace = self.stack.workspace
        first = len(workspace.audit)
        try:
            client, model_name = await asyncio.to_thread(self.models)
            person = workspace.user(run["person"])
            if run["server"] == "oauth":
                consent = Consent(run["person"], lambda view: self.ask_consent(run_id, run["person"], view))
                storage = self.tokens.setdefault(run["person"], MemoryTokens())
                async with signed_in(RESOURCE, consent, storage) as http:
                    result = await ask(RESOURCE, http, person, run["message"], client, model_name, run["steps"].append)
            else:
                result = await ask(SHARED_KEY_URL, None, person, run["message"], client, model_name, run["steps"].append)
            run.update(reply=result["reply"], cost=result["cost"])
        # A missing key raises SystemExit and a refused consent PermissionError; either must end the run.
        except (Exception, SystemExit) as error:
            run.update(error=str(error))
        finally:
            granted = self.granted(run["server"], run["person"])
            # Each entry this run wrote is tagged with whom it was for, and checked against their grant.
            for entry in workspace.audit[first:]:
                entry["asked_by"] = workspace.name_of(run["person"])
                entry["outside"] = outside_grant(entry, run["person"], granted, workspace)
            run["audit"] = workspace.audit[first:]
            # Done first: a page that sees no run in progress must find this one finished.
            run["done"] = True
            self.consent, self.current = None, None

    async def ask_consent(self, run_id: str, person: str, view: dict) -> list[str]:
        """Park the run until the person ticks scopes on the page's consent sheet."""
        answer = self.loop.create_future()
        self.consent = {**view, "person": person, "run": run_id, "answer": answer}
        return await answer

    def answer_consent(self, scopes: list[str]) -> bool:
        if not self.consent:
            return False
        answer = self.consent["answer"]
        self.loop.call_soon_threadsafe(lambda: answer.done() or answer.set_result(scopes))
        return True

    def disconnect(self, person: str) -> None:
        """Revoke the assistant's grant: the tokens stop working, and the next request asks again."""
        authority = self.stack.authority
        for token in [token for token, found in authority.tokens.items() if found.subject == person]:
            authority.tokens.pop(token)
        for token in [token for token, found in authority.refresh_tokens.items() if found.subject == person]:
            authority.refresh_tokens.pop(token)
        authority.grants.pop((person, CLIENT_NAME), None)
        self.tokens.pop(person, None)

    def act(self, person: str, issue_id: str, status: str) -> dict:
        """The person does it themselves in Larch's web app, signed in as themselves, with their role's rights."""
        workspace = self.stack.workspace
        session = "lws_" + secrets.token_urlsafe(16)
        workspace.api_keys[session] = Credential(person, frozenset(workspace.role_scopes(person)), WEB_APP, "signed-in session")
        try:
            workspace.call(session, "set_status", issue_id=issue_id, status=status)
            return {"ok": True}
        except Refused as refusal:
            return {"ok": False, "error": refusal.reason}
        finally:
            workspace.api_keys.pop(session)
            workspace.audit[-1]["asked_by"] = workspace.name_of(person)

    def reset(self) -> None:
        workspace = self.stack.workspace
        workspace.data = copy.deepcopy(workspace.original)
        workspace.audit.clear()
        for person in [user["id"] for user in workspace.data["users"]]:
            self.disconnect(person)


def handler(larch: Larch) -> type[BaseHTTPRequestHandler]:
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
            """Only this machine's own page may call: a foreign page could otherwise grant scopes for you."""
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
                return self.send(200, json.dumps(larch.state()).encode())
            run_id = path.removeprefix("/api/run/")
            if path.startswith("/api/run/") and run_id in larch.runs:
                return self.send(200, json.dumps(larch.runs[run_id]).encode())
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            person = str(body.get("person") or "")
            known = larch.stack.workspace.user(person) is not None and person != "svc-mcp"
            if self.path == "/api/run":
                message = str(body.get("message") or "").strip()
                if body.get("server") not in SERVERS or not known or not message:
                    return self.send(400, b"a server, a person and a message", "text/plain")
                run_id = larch.start(body["server"], person, message, body.get("request"))
                return self.send(200, json.dumps({"id": run_id}).encode()) if run_id else self.send(409, b"a request is running", "text/plain")
            if self.path == "/api/consent":
                scopes = [scope for scope in body.get("scopes") or [] if scope in NEEDS.values()]
                return self.send(200 if larch.answer_consent(scopes) else 409, json.dumps({"ok": True}).encode())
            if self.path == "/api/disconnect" and known:
                larch.disconnect(person)
                return self.send(200, json.dumps({"ok": True}).encode())
            if self.path == "/api/act" and known:
                return self.send(200, json.dumps(larch.act(person, str(body.get("issue")), str(body.get("status")))).encode())
            if self.path == "/api/reset":
                larch.reset()
                return self.send(200, json.dumps({"ok": True}).encode())
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(larch: Larch, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(larch))


def main(port: int | None) -> int:
    from larch.cli import model

    larch = Larch(model)
    server = serve(larch, port or PAGE_PORT)
    print(f"Larch for Copperline Payments on http://127.0.0.1:{port or PAGE_PORT}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    larch.stop()
    return 0
