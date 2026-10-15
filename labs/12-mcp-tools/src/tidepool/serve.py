"""`store serve`: Tidepool Coffee's store admin, with the assistant panel driving either MCP server."""

import asyncio
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from mcp.client import Client

from tidepool.assistant import ask
from tidepool.review import NAMES, SERVERS, cost, judge, tasks
from tidepool.store import Store

PAGE = Path(__file__).with_name("page.html")
REVIEW = Path("out/review.json")


async def tool_list(server: str) -> list[dict]:
    async with Client(SERVERS[server](Store())) as mcp:
        tools = (await mcp.list_tools()).tools
    return [{"name": tool.name, "description": tool.description or "",
             "read_only": bool(tool.annotations and tool.annotations.read_only_hint),
             "destructive": bool(tool.annotations and tool.annotations.destructive_hint),
             "size": len(json.dumps(tool.model_dump(by_alias=True, exclude_none=True)))} for tool in tools]


class Admin:
    """Runs assistant requests on background threads; the page polls each run for steps and the store."""

    def __init__(self, models, review_file: Path = REVIEW):
        self.make_models, self.models_cache = models, None
        self.review_file, self.runs, self.lock = review_file, {}, threading.Lock()
        self.tools = {server: asyncio.run(tool_list(server)) for server in SERVERS}

    def models(self) -> tuple:
        with self.lock:
            self.models_cache = self.models_cache or self.make_models()
            return self.models_cache

    def state(self) -> dict:
        review = json.loads(self.review_file.read_text()) if self.review_file.exists() else None
        return {"store": Store().data, "tasks": tasks(), "names": NAMES, "tools": self.tools,
                "review": {server: result["score"] for server, result in review.items()} if review else None}

    def start(self, server: str, message: str, task_id: str | None, follows: str | None) -> str:
        earlier = self.runs.get(follows) if follows else None
        # A follow-up answers the assistant's question on the same store, so "the 1kg" lands on that run.
        store = earlier["store"] if earlier else Store()
        history = earlier["history"] if earlier else []
        task = next((each for each in tasks() if each["id"] == task_id), None) if not earlier else earlier["task"]
        run = {"steps": [], "done": False, "server": server, "store": store, "task": task,
               "history": history + [{"role": "user", "content": message}]}
        run_id = uuid.uuid4().hex[:8]
        self.runs[run_id] = run

        def work():
            try:
                client, model_name = self.models()
                result = asyncio.run(ask(SERVERS[server](store), message, client, model_name,
                                         run["steps"].append, history))
                run["history"].append({"role": "assistant", "content": result["reply"]})
                run.update(reply=result["reply"], cost=cost(model_name, result["tokens"]))
                # Only the seeded ask has an answer key. A typed request, or the merchant's answer to
                # "which one?", goes past what the key covers, so it is shown, not scored.
                if task and not earlier and message == task["ask"]:
                    ok, why = judge(task, store.changes(), result["reply"])
                    run.update(verdict={"ok": ok, "why": why, "task": task["id"]})
            except Exception as error:  # shown on the page, so a bad key reads as a message, not a hang
                run.update(error=str(error))
            run["done"] = True

        threading.Thread(target=work, daemon=True).start()
        return run_id

    def view(self, run_id: str) -> dict:
        run = self.runs[run_id]
        return {**{key: value for key, value in run.items() if key not in ("store", "task", "history")},
                "data": run["store"].data, "changes": run["store"].changes()}


def handler(admin: Admin) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: a foreign page could otherwise issue refunds."""
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
                return self.send(200, json.dumps(admin.state()).encode(), "application/json")
            run_id = path.removeprefix("/api/run/")
            if path.startswith("/api/run/") and run_id in admin.runs:
                return self.send(200, json.dumps(admin.view(run_id)).encode(), "application/json")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            message = str(body.get("message") or "").strip()
            if self.path != "/api/run":
                return self.send(404, b"not found", "text/plain")
            if body.get("server") not in SERVERS or not message or (body.get("follows") and body["follows"] not in admin.runs):
                return self.send(400, b"a server, a message, and any follow-up of a known run", "text/plain")
            run_id = admin.start(body["server"], message, body.get("task"), body.get("follows"))
            self.send(200, json.dumps({"id": run_id}).encode(), "application/json")

    return Handler


def serve(admin: Admin, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(admin))


def main(port: int) -> int:
    from tidepool.cli import model

    server = serve(Admin(model), port)
    print(f"Tidepool store admin on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
