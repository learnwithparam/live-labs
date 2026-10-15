"""`search serve`: Copperline's company search, viewed as any employee, on this machine only."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from copperline.access import Authorizer, describe, load_store
from copperline.answer import MODES, answer, gather
from copperline.retrieve import Index, chunks_of
from copperline.sync import build, documents, labels, people, record_decision

PAGE = Path(__file__).with_name("page.html")
LEAKS = Path("out/leaks.json")


class Search:
    """Holds the index and the OpenFGA client; the model client is made on the first question."""

    def __init__(self, make_model, authorizer=None, leaks_file: Path = LEAKS):
        self.make_model, self.model = make_model, None
        self.authorizer = authorizer or Authorizer()
        self.index, self.staff, self.names = Index(chunks_of(documents())), people(), labels()
        self.leaks_file, self.lock = leaks_file, threading.Lock()

    def state(self) -> dict:
        from copperline.leaks import pairs

        leaks = json.loads(self.leaks_file.read_text()) if self.leaks_file.exists() else None
        titles = {document["id"]: document["title"] for document in documents()}
        held = [{**each, "title": titles[each["document"]]} for each in build()["held"]]
        return {"people": list(self.staff.values()), "modes": list(MODES), "held": held,
                "questions": [{"id": pair["id"], "question": pair["question"], "denied": pair["denied"],
                               "allowed": pair["allowed"]} for pair in pairs()],
                "leaks": leaks and {"model": leaks["model"], "pairs": leaks["pairs"], **leaks["score"]}}

    def ask(self, user: str, question: str, mode: str) -> dict:
        gathered = gather(mode, user, question, self.index, self.authorizer if mode != "naive" else None)
        with self.lock:
            self.model = self.model or self.make_model()
        client, model_name = self.model
        reply = asyncio.run(answer(client, model_name, mode, self.staff[user], question, gathered["chunks"]))
        first = {}
        for chunk in gathered["chunks"]:
            first.setdefault(chunk["document"], chunk)
        # Checked after the answer, for the page only: in naive mode it shows which chunks should never have been sent.
        allowed = self.authorizer.allowed(user, list(first))
        sources = [{"document": document, "title": chunk["title"], "source": chunk["source"], "place": chunk["place"],
                    "text": "\n\n".join(each["text"] for each in gathered["chunks"] if each["document"] == document),
                    "allowed": document in allowed, "cited": document in reply["cited"],
                    "why": describe(self.authorizer.why(user, document), self.names) if document in allowed else []}
                   for document, chunk in first.items()]
        return {"answer": reply["text"], "sources": sources, "dropped": gathered["dropped"],
                "checked": gathered["checked"], "cost": reply["cost"], "model": model_name}

    def decide(self, document_id: str, decision: str) -> dict:
        held = next(each for each in build()["held"] if each["document"] == document_id)
        unknown = held["reasons"][0].split("'")[1]
        record_decision(document_id, {"map": {unknown: "security"}} if decision == "map" else {"keep": True})
        # The decision changes tuples, so OpenFGA gets a fresh store built from the sources plus the decision.
        load_store(build()["tuples"])
        self.authorizer = Authorizer()
        return self.state()


def handler(search: Search) -> type[BaseHTTPRequestHandler]:
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
            """Only this machine's own page may call: a foreign page could otherwise read as any employee."""
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
                return self.send(200, json.dumps(search.state()).encode(), "application/json")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            try:
                if self.path == "/api/ask":
                    question = str(body.get("question") or "").strip()
                    if body.get("user") not in search.staff or body.get("mode") not in MODES or not question:
                        return self.send(400, b"a known user, a mode and a question", "text/plain")
                    result = search.ask(body["user"], question, body["mode"])
                elif self.path == "/api/decide" and body.get("decision") in ("map", "keep"):
                    result = search.decide(str(body.get("document")), body["decision"])
                else:
                    return self.send(404, b"not found", "text/plain")
            # A missing key raises SystemExit; the page shows the message instead of a hung request.
            except (Exception, SystemExit) as error:
                return self.send(500, json.dumps({"error": str(error)}).encode(), "application/json")
            self.send(200, json.dumps(result).encode(), "application/json")

    return Handler


def serve(search: Search, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(search))


def main(port: int) -> int:
    from copperline.config import model_client

    server = serve(Search(model_client), port)
    print(f"Copperline search on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
