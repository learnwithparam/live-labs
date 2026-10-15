"""The way the bank answered auditors before: grep each team's app log by hand.

Each function does what an engineer would do at a terminal, and counts it: every folder listed, every
grep run and every matching line read. Where the logs cannot answer, it says why instead of guessing,
and where they answer wrongly with confidence, the grader catches it.
"""

import json
import re
from pathlib import Path


class Terminal:
    """Runs the greps and keeps the tally: the steps a person takes is the honest cost of this path."""

    def __init__(self, folder: Path):
        self.folder, self.steps, self.files, self.lines_read = folder, [], set(), 0

    def list_folder(self) -> list[str]:
        self.steps.append(f"ls {self.folder.name}/")
        return sorted(path.name for path in self.folder.glob("*"))

    def grep(self, pattern: str, file_name: str) -> list[str]:
        self.steps.append(f"grep -E '{pattern}' {file_name}")
        self.files.add(file_name)
        path = self.folder / file_name
        found = [line for line in path.read_text().splitlines() if re.search(pattern, line)] if path.exists() else []
        self.lines_read += len(found)
        return found


def field(line: str, name: str) -> str | None:
    match = re.search(rf"\b{name}=(\"[^\"]*\"|\S+)", line)
    return match.group(1).strip('"') if match else None


def in_days(stamp: str, start: str, end: str) -> bool:
    return start <= stamp[:10] <= end


def cannot(why: str) -> dict:
    return {"cannot": why}


def customer_facing(terminal: Terminal) -> dict:
    terminal.list_folder()
    return cannot("No list of AI systems exists. The folder names four log files and none says who reads the "
                  "text. You would email each team and wait.")


def disclosure_status(terminal: Terminal) -> dict:
    for name in terminal.list_folder():
        terminal.grep("disclos", name)
    return cannot("No log records whether the customer was told they were talking to an AI.")


def conversation(terminal: Terminal, system: str, subject: str, day: str) -> dict:
    file_name, key = {"help-chat": ("chat-service.log", "customer"), "staff-search": ("staff-search.log", "user")}[system]
    lines = [line for line in terminal.grep(f"{key}={subject}\\b", file_name)
             if day in line.replace(" ", "T")[:40]]
    ids = [field(line, "req") for line in lines]
    # The calls are found; what was said is not. That is the gap the audit log closes.
    return {"requests": ids, "cannot": "The log keeps the first few words of the question and the reply's length, "
            "never the reply. What the customer was told cannot be shown."}


def provenance(terminal: Terminal, system: str, subject: str, day: str) -> dict:
    lines = [line for line in terminal.grep(f"customer={subject}\\b", "chat-service.log") if line.startswith(day)]
    models = sorted({field(line, "model") for line in lines if field(line, "model")})
    return {"models": models, "versions": [], "cannot": "The chat log names the model without its provider, and "
            "no log names the prompt version."}


def subject_history(terminal: Terminal, subject: str, start: str, end: str) -> dict:
    ids = []
    for name in terminal.list_folder():
        for line in terminal.grep(subject, name):
            stamp = json.loads(line)["ts"] if line.startswith("{") else (field(line, "ts") or line.strip("["))
            if in_days(stamp, start, end):
                ids.append(field(line, "req") or json.loads(line).get("req"))
    # The fraud log keys on the transaction and the mailer on its ticket, so a customer number misses both.
    return {"requests": sorted(ids)}


def system_card(terminal: Terminal, system: str) -> dict:
    terminal.list_folder()
    return cannot("Owners and allowed models are not in any log. You would ask Financial Crime.")


def unregistered(terminal: Terminal) -> dict:
    services = terminal.list_folder()
    # Four files, four services: a person concludes "nothing else", and that conclusion is the risk.
    return {"systems": [], "refused": [], "answered": [], "note": f"Only {len(services)} services write logs here."}


def unlisted_models(terminal: Terminal) -> dict:
    seen = set()
    for name in terminal.list_folder():
        for line in terminal.grep("model|gpt|claude", name):
            seen |= set(re.findall(r"(?:model=)?([a-z]+/[\w.-]+|gpt-[\w.-]+)", line))
    # Seeing a model name is not the answer: without a list of what each system may use, nothing is "unlisted".
    return {"models": [], "refused": [], "answered": [],
            "note": f"Models seen: {', '.join(sorted(seen)) or 'none'}. No list says which each system may use."}


def count(terminal: Terminal, system: str, start: str, end: str) -> dict:
    lines = [line for line in terminal.grep("INFO chat.reply", "chat-service.log") if in_days(line, start, end)]
    return {"count": len(lines)}


def undisclosed(terminal: Terminal) -> dict:
    for name in terminal.list_folder():
        terminal.grep("disclos", name)
    return cannot("No log records a disclosure, so no reply can be shown to have had one.")


def system_activity(terminal: Terminal, system: str, start: str, end: str) -> dict:
    events = [json.loads(line) for line in terminal.grep('"req"', "mailer.jsonl")]
    events = [event for event in events if in_days(event["ts"], start, end)]
    return {"answered": [event["req"] for event in events if event["event"] == "auto_reply_sent"],
            "refused": [event["req"] for event in events if event["event"] == "auto_reply_skipped"]}


def integrity(terminal: Terminal) -> dict:
    terminal.list_folder()
    return cannot("The logs are plain files that anyone with access can edit. Nothing would show an edit.")


def answer_all(folder: Path, requests: list[dict]) -> dict:
    """The same labelled requests on the old path. Correct needs every field, and an answer with a gap is not complete."""
    rows = []
    for request in requests:
        terminal = Terminal(folder)
        result = globals()[request["kind"]](terminal, **request["params"])
        correct = all(result.get(key) == value for key, value in request["expected"].items())
        rows.append({"id": request["id"], "result": result, "steps": terminal.steps, "files": sorted(terminal.files),
                     "lines_read": terminal.lines_read, "correct": correct, "complete": correct and "cannot" not in result,
                     "why": result.get("cannot") or result.get("note")})
    return {"rows": rows, "answered": sum(row["complete"] for row in rows), "total": len(rows),
            "steps": sum(len(row["steps"]) for row in rows), "lines_read": sum(row["lines_read"] for row in rows)}
