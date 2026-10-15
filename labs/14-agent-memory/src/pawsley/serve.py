"""`support serve`: Pawsley's support chat with the customer's memory panel, on 127.0.0.1 only."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pawsley import keeper
from pawsley.agent import chat
from pawsley.keeper import NAMES, POLICIES, pending
from pawsley.memory import all_memories
from pawsley.review import customers, score, totals, verdict

PAGE = Path(__file__).with_name("page.html")


class Desk:
    """Both policies' stores, as the review left them. One lock per store: embedded Qdrant is single-writer."""

    def __init__(self, open_store, make_model, review_file: Path | None = None):
        self.open_store, self.make_model, self.billed, self.review_file = open_store, make_model, None, review_file
        self.stores = {policy: open_store(policy) for policy in POLICIES}
        self.locks = {policy: threading.Lock() for policy in POLICIES}
        self.truth = {each["id"]: each for each in customers()}
        # Scoring runs twelve searches per policy, so a score is kept until that store changes.
        self.scores: dict[str, list[dict]] = {}

    def model(self):
        # The key is needed only to chat, so the page and the memory panel open without one.
        self.billed = self.billed or self.make_model()
        return self.billed

    def state(self) -> dict:
        board = {}
        for policy in POLICIES:
            with self.locks[policy]:
                if policy not in self.scores:
                    self.scores[policy] = [score(self.stores[policy], truth) for truth in self.truth.values()]
                rows = self.scores[policy]
            board[policy] = {"score": totals(rows, {"calls": 0, "cost": 0.0}), "rows": rows}
        # The live score moves as you chat and edit; the review's bill says what replaying everyone cost.
        bills = {}
        if self.review_file and self.review_file.exists():
            saved = json.loads(self.review_file.read_text())
            bills = {policy: {"calls": saved[policy]["score"]["calls"], "cost": saved[policy]["score"]["cost"],
                              "model": saved[policy]["model"]} for policy in POLICIES if policy in saved}
        return {"customers": list(self.truth.values()), "names": NAMES, "policies": board, "bills": bills}

    def memories(self, policy: str, customer: str) -> list[dict]:
        memory, truth = self.stores[policy], self.truth[customer]
        with self.locks[policy]:
            found = all_memories(memory, customer)
            changes = {item["id"]: [row for row in memory.history(item["id"]) if row["event"] == "UPDATE"
                       and row["old_memory"] != row["new_memory"]] for item in found}
        return [{"id": item["id"], "text": item["memory"], "pending": pending(item),
                 "question": (item.get("metadata") or {}).get("question"),
                 "conflicts_with": next((other["memory"] for other in found
                                         if other["id"] == (item.get("metadata") or {}).get("conflicts_with")), None),
                 "was": changes[item["id"]][-1]["old_memory"] if changes[item["id"]] else None,
                 "verdict": verdict(item["memory"], truth)} for item in found]

    def act(self, action: str, body: dict) -> dict:
        policy, customer = body["policy"], body["customer"]
        memory = self.stores[policy]
        with self.locks[policy]:
            self.scores.pop(policy, None)
            if action == "chat":
                earlier = [turn for turn in body.get("earlier") or [] if turn.get("role") in ("user", "assistant")]
                # Mem0 extracts facts with the store's own model, so the store gets the billed one too.
                memory.llm = self.model()
                return chat(memory, memory.llm, policy, customer, str(body["message"]), earlier[-8:])
            if action == "edit":
                keeper.edit(memory, body["id"], str(body["text"]))
            elif action == "forget":
                keeper.forget(memory, customer, body["id"])
            elif action == "settle":
                return keeper.settle(memory, customer, body["id"], bool(body["use_new"]))
        return {"ok": True}


def handler(desk: Desk):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def local(self) -> bool:
            # A web page elsewhere could post to a localhost port; only this page may change memories.
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            parts = urlsplit(self.path)
            query = {key: values[0] for key, values in parse_qs(parts.query).items()}
            if parts.path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if parts.path == "/api/state":
                return self.send(200, json.dumps(desk.state()).encode(), "application/json")
            if parts.path == "/api/memories" and query.get("policy") in POLICIES and query.get("customer") in desk.truth:
                return self.send(200, json.dumps(desk.memories(query["policy"], query["customer"])).encode(), "application/json")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            action = self.path.removeprefix("/api/")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if action not in ("chat", "edit", "forget", "settle"):
                return self.send(404, b"not found", "text/plain")
            if body.get("policy") not in POLICIES or body.get("customer") not in desk.truth:
                return self.send(400, b"a policy and a known customer", "text/plain")
            try:
                result = desk.act(action, body)
            except (KeyError, ValueError, AttributeError, TypeError) as error:
                return self.send(400, str(error).encode(), "text/plain")
            except SystemExit as error:  # a missing key reads as a message on the page, not a crash
                return self.send(503, str(error).encode(), "text/plain")
            self.send(200, json.dumps(result).encode(), "application/json")

    return Handler


def serve(desk: Desk, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(desk))


def main(port: int) -> int:
    from pawsley.cli import REVIEW, model, open_store
    from pawsley.memory import BilledModel, local_embedder

    embedder = local_embedder()
    # Search and edits need only the embedder; the billed model is swapped in on the first chat.
    placeholder = BilledModel(None, "openai/gpt-6-luna")
    desk = Desk(lambda policy: open_store(policy, placeholder, embedder), model, REVIEW)
    server = serve(desk, port)
    print(f"Pawsley support chat on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
