"""`tune serve`: Corvane's model release console, local only, on the files the CLI wrote to out/."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from corvane.config import CASCADE_MARGIN, CLASS_FLOOR, COVERAGE_FLOOR, MARGIN, SMALL_MODEL_HOST
from corvane.labels import CATEGORIES, DECISIONS, read_jsonl, write_jsonl
from corvane.release import decide_release, decision_row

PAGE = Path(__file__).with_name("page.html")


class Console:
    """Reads the scores, the release and the queue from disk on every request, so a new CLI run shows up."""

    def __init__(self, out_dir: Path = Path("out"), decisions_file: Path = DECISIONS):
        self.out_dir, self.decisions_file = out_dir, decisions_file

    def read(self, name: str, empty):
        path = self.out_dir / name
        return json.loads(path.read_text()) if path.exists() else empty

    def state(self) -> dict:
        scores, queue = self.read("scores.json", {}), self.read("queue.json", [])
        decided = {row["id"]: row["label"] for row in read_jsonl(self.decisions_file)}
        models = {key: {name: value for name, value in result.items() if name != "predictions"}
                  for key, result in scores.items()}
        # Until `tune cascade` runs there is no threshold, so the release holds rather than guessing one.
        cascade = self.read("release.json", None) or {"decision": "hold", "checks": [], "routed_items": [],
                                                      "reasons": ["No cascade yet. Run `uv run tune cascade`."]}
        sent_up = [{key: row.get(key) for key in ("id", "descriptor", "amount", "posted", "reason", "tuned", "large")}
                   | {"decided": decided.get(row["id"])} for row in queue if row["route"] == "large"]
        return {"categories": CATEGORIES, "models": models, "cascade": cascade,
                "plain": decide_release(scores.get("tuned"), scores.get("large")),
                "misses": self.misses(scores), "train": self.read("train.json", None),
                "queue": {"total": len(queue), "small": len(queue) - len(sent_up), "sent_up": sent_up},
                "rules": {"margin": MARGIN, "class_floor": CLASS_FLOOR, "cascade_margin": CASCADE_MARGIN,
                          "coverage_floor": COVERAGE_FLOOR},
                "host": SMALL_MODEL_HOST}

    def misses(self, scores: dict) -> list[dict]:
        """Every held-out item the tuned model got wrong, with what the large model and the reviewer said."""
        if "tuned" not in scores:
            return []
        from corvane.labels import HELDOUT

        truth = {item["id"]: item for item in read_jsonl(self.out_dir / HELDOUT.name)}
        large = {row["id"]: row["label"] for row in scores.get("large", {}).get("predictions", [])}
        return [{"id": row["id"], "descriptor": truth[row["id"]]["descriptor"], "amount": truth[row["id"]]["amount"],
                 "truth": truth[row["id"]]["label"], "tuned": row["label"], "large": large.get(row["id"])}
                for row in scores["tuned"]["predictions"] if row["id"] in truth and row["label"] != truth[row["id"]]["label"]]

    def decide(self, item_id: str, label: str) -> dict:
        """A reviewer's label for an item the small model sent up. It is saved where the next export picks it up."""
        queue = {row["id"]: row for row in self.read("queue.json", [])}
        if item_id not in queue or label not in CATEGORIES:
            raise KeyError(item_id)
        kept = [row for row in read_jsonl(self.decisions_file) if row["id"] != item_id]
        write_jsonl(self.decisions_file, kept + [decision_row(queue[item_id], label, "you")])
        return self.state()


def handler(console: Console) -> type[BaseHTTPRequestHandler]:
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
            """Only this machine's own page may call: the queue holds customers' card transactions."""
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
                return self.send(200, json.dumps(console.state()).encode(), "application/json")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            if urlsplit(self.path).path != "/api/review":
                return self.send(404, b"not found", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            try:
                return self.send(200, json.dumps(console.decide(str(body.get("id")), str(body.get("label")))).encode(),
                                 "application/json")
            except KeyError:
                return self.send(400, b"an id from the review queue and one category", "text/plain")

    return Handler


def serve(console: Console, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(console))


def main(port: int) -> int:
    server = serve(Console(), port)
    print(f"Corvane model release console on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
