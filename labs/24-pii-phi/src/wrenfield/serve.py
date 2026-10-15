"""`scribe serve`: Wrenfield's transcript editor, local only, on the annotated visits."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from wrenfield.detect import REVIEW_BELOW, Span, detector, regex_spans
from wrenfield.grade import grade, grade_visit, visits
from wrenfield.redact import PLACEHOLDER, Vault, reidentify
from wrenfield.scribe import document, leaked_phi, prepare

PAGE = Path(__file__).with_name("page.html")
NOTES = Path("out/notes.json")
VAULT = Path("out/vault.json")
TYPES = ["NAME", "DATE", "PHONE", "EMAIL", "SSN", "MRN", "ID", "ADDRESS"]


def find(text: str, language: str) -> list[Span]:
    return detector().spans(text, language)


def why_unsure(span: Span) -> str:
    if span.source == "SpokenNumberRecognizer":
        return (f"A number read aloud with no cue word nearby, such as phone, MRN or social. It scored {span.score}, "
                f"below {REVIEW_BELOW}, so it is redacted until you decide.")
    return f"{span.source} scored this {span.score}, below {REVIEW_BELOW}, so it is redacted until you decide."


def line_segments(text: str, spans: list[Span], missed: list[dict], tokens: dict, statuses: dict) -> list[list[dict]]:
    """The transcript cut into runs per line, each run plain or one identifier, with its absolute offset."""
    owner: list[tuple | None] = [None] * len(text)
    for annotation in missed:
        for position in range(annotation["start"], annotation["end"]):
            owner[position] = ("missed", annotation["start"], annotation["type"], None)
    for span in spans:
        for position in range(span.start, span.end):
            owner[position] = (statuses[(span.start, span.end)], span.start, span.type, tokens.get((span.start, span.end)))
    lines, offset = [], 0
    for line in text.split("\n"):
        pieces, run_start, line_end = [], offset, offset + len(line)
        for position in range(offset, line_end + 1):
            if position == line_end or owner[position] != owner[run_start]:
                if position > run_start:
                    label = owner[run_start]
                    pieces.append({"text": text[run_start:position], "start": run_start,
                                   "status": label[0] if label else None, "type": label[2] if label else None,
                                   "token": label[3] if label else None})
                run_start = position
        lines.append(pieces)
        offset = line_end + 1
    return lines


def redacted_segments(redacted: str, vault: Vault) -> list[list[dict]]:
    lines = []
    for line in redacted.split("\n"):
        pieces, cursor = [], 0
        for match in PLACEHOLDER.finditer(line):
            if match.start() > cursor:
                pieces.append({"text": line[cursor:match.start()]})
            entry = vault.entries.get(match.group(), {})
            pieces.append({"text": match.group(), "token": match.group(), "type": entry.get("type")})
            cursor = match.end()
        if cursor < len(line):
            pieces.append({"text": line[cursor:]})
        lines.append(pieces)
    return lines


class Desk:
    """The editor's state: clinician reviews and written notes, kept per visit while the server runs."""

    def __init__(self, make_models, notes_file: Path = NOTES, vault_file: Path = VAULT):
        self.make_models, self.models_cache, self.lock = make_models, None, threading.Lock()
        self.notes_file, self.vault_file = notes_file, vault_file
        self.visits = {one_visit["id"]: one_visit for one_visit in visits()}
        self.reviews: dict[str, dict] = {}
        saved = json.loads(notes_file.read_text()) if notes_file.exists() else {"records": {}}
        self.records, self.model_name = saved["records"], saved.get("model")
        self.vaults = json.loads(vault_file.read_text()) if vault_file.exists() else {}
        english = [one_visit for one_visit in self.visits.values() if one_visit["language"] == "en"]
        self.grades = {"presidio": grade(english, lambda one: find(one["transcript"], "en")),
                       "regex": grade(english, lambda one: regex_spans(one["transcript"]))}

    def models(self) -> tuple:
        with self.lock:
            self.models_cache = self.models_cache or self.make_models()
            return self.models_cache

    def summary(self) -> dict:
        best, naive = self.grades["presidio"], self.grades["regex"]
        rows = []
        for visit_id, one_visit in self.visits.items():
            prepared = prepare(one_visit, find, self.reviews.get(visit_id))
            rows.append({"id": visit_id, "time": one_visit["time"], "reason": one_visit["reason"],
                         "clinician": one_visit["clinician"], "status": prepared["status"],
                         "unsure": len(prepared.get("unsure", [])), "noted": visit_id in self.records
                         and self.records[visit_id]["note"] is not None})
        return {"visits": rows, "model": self.model_name,
                "metric": {"spans": best["spans"], "missed": best["missed"], "extra": best["extra"],
                           "detected": best["detected"], "by_type": best["by_type"], "regex_missed": naive["missed"],
                           "held": sum(row["status"] == "held" for row in rows),
                           "waiting": sum(row["unsure"] > 0 or row["status"] == "held" for row in rows)}}

    def view(self, visit_id: str) -> dict:
        one_visit = self.visits[visit_id]
        review = self.reviews.get(visit_id, {})
        prepared = prepare(one_visit, find, review)
        info = {key: one_visit[key] for key in ("id", "time", "reason", "clinician", "language")}
        record = self.records.get(visit_id)
        if prepared["status"] == "held":
            plain = [[{"text": line, "start": None, "status": None}] for line in one_visit["transcript"].split("\n")]
            return {**info, "status": "held", "held_reason": prepared["reason"], "lines": plain, "redacted": [],
                    "annotated": len(one_visit["phi"]), "review": [], "note": None}
        spans, vault, text = prepared["spans"], prepared["vault"], one_visit["transcript"]
        graded = grade_visit(one_visit, spans)
        extra = {(item["start"], item["end"]) for item in graded["extra"]}
        statuses = {(span.start, span.end): "clinician" if span.source == "Clinician" else "unsure" if span.unsure
                    else "extra" if (span.start, span.end) in extra else "caught" for span in spans}
        tokens = {(span.start, span.end): vault.token_for(span.type, text[span.start:span.end]) for span in spans}
        cards = [{"kind": "unsure", "start": span.start, "end": span.end, "type": span.type,
                  "text": text[span.start:span.end], "why": why_unsure(span)} for span in prepared["unsure"]]
        cards += [{"kind": "missed", "start": annotation["start"], "end": annotation["end"], "type": annotation["type"],
                   "text": annotation["text"], "why": "The annotators marked this as PHI and no recognizer found it. "
                   "It went to the model as written."} for annotation in graded["missed"]]
        note = None
        if record and record["note"] is not None:
            stored_vault = Vault(entries=self.vaults.get(visit_id, {}))
            restored, unknown = reidentify(record["note"], stored_vault)
            sent = [message["content"] for message in record["messages"]] + [record["note"]]
            note = {"raw": record["note"], "restored": restored, "unknown": unknown, "usage": record["usage"],
                    "leaked": leaked_phi(one_visit, sent), "stale": record.get("review", {}) != review}
        return {**info, "status": "sent", "held_reason": None, "annotated": graded["total"],
                "missed": len(graded["missed"]), "extra": len(graded["extra"]),
                "lines": line_segments(text, spans, graded["missed"], tokens, statuses),
                "redacted": redacted_segments(prepared["redacted"], vault), "review": cards,
                "decisions": review, "note": note, "types": TYPES}

    def decide(self, visit_id: str, action: str, start: int, end: int, kind: str) -> dict:
        """The clinician's call on one span: confirm it, release it to the model, or add one that was missed."""
        review = self.reviews.setdefault(visit_id, {"add": [], "release": [], "confirm": []})
        item = {"start": start, "end": end, "type": kind}
        review[action].append(item)
        return self.view(visit_id)

    def write(self, visit_id: str) -> dict:
        client, model_name = self.models()
        review = self.reviews.get(visit_id, {})
        record, vault = document(self.visits[visit_id], find, client, model_name, review)
        record["review"] = review
        self.records[visit_id], self.vaults[visit_id], self.model_name = record, vault.entries, model_name
        self.notes_file.parent.mkdir(exist_ok=True)
        self.notes_file.write_text(json.dumps({"model": model_name, "records": self.records}, indent=1))
        self.vault_file.write_text(json.dumps(self.vaults, indent=1))
        return self.view(visit_id)


def handler(desk: Desk) -> type[BaseHTTPRequestHandler]:
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
            """Only this machine's own page may call: the transcripts are patient records."""
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
                return self.send(200, json.dumps(desk.summary()).encode(), "application/json")
            visit_id = path.removeprefix("/api/visit/")
            if path.startswith("/api/visit/") and visit_id in desk.visits:
                return self.send(200, json.dumps(desk.view(visit_id)).encode(), "application/json")
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            parts = urlsplit(self.path).path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "visit"] or parts[2] not in desk.visits:
                return self.send(404, b"not found", "text/plain")
            visit_id, action = parts[2], parts[3]
            if action == "note":
                try:
                    return self.send(200, json.dumps(desk.write(visit_id)).encode(), "application/json")
                # A missing key raises SystemExit; the page shows the message instead of hanging.
                except (Exception, SystemExit) as error:
                    return self.send(502, json.dumps({"error": str(error)}).encode(), "application/json")
            text_length = len(desk.visits[visit_id]["transcript"])
            start, end, kind = body.get("start"), body.get("end"), body.get("type")
            if (action not in ("add", "release", "confirm") or not isinstance(start, int) or not isinstance(end, int)
                    or not 0 <= start < end <= text_length or kind not in TYPES):
                return self.send(400, b"an action of add, release or confirm, and a span inside the transcript",
                                 "text/plain")
            self.send(200, json.dumps(desk.decide(visit_id, action, start, end, kind)).encode(), "application/json")

    return Handler


def serve(desk: Desk, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(desk))


def main(port: int) -> int:
    from wrenfield.cli import model

    print("Loading the spaCy model for Presidio...")
    server = serve(Desk(model), port)
    print(f"Wrenfield transcript editor on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
