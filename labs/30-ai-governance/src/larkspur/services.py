"""The bank's four services, replaying a fortnight of real traffic through the gateway.

Each service also keeps the log its team always kept, in its own format. Those logs are how the bank
answered auditors before the inventory: they are what the naive path greps. The truncation and the
missing fields are the ones teams choose for good reasons, such as keeping card details out of logs.
"""

import json
from pathlib import Path

from larkspur.auditlog import AuditLog
from larkspur.gateway import Outcome, governed_call
from larkspur.inventory import System

PROMPT_CHARS = 32  # the chat team cuts prompts short so card numbers a customer types never reach the log


def traffic(data_dir: Path) -> list[dict]:
    rows = [json.loads(line) for line in (data_dir / "traffic.jsonl").read_text().splitlines() if line.strip()]
    # Time order is chain order, so "what happened next" in the log is what happened next in the bank.
    return sorted(rows, key=lambda row: row["at"])


def app_log_line(request: dict, outcome: Outcome) -> tuple[str, str] | None:
    """The line each team's service writes, and the file it goes to. The marketing script writes none."""
    system, stamp, request_id = request["system"], request["at"], request["id"]
    model_short = (outcome.record.get("gen_ai.request.model") or "").split("/")[-1]
    if system == "help-chat":
        if outcome.status != "answered":
            return "chat-service.log", f"{stamp} ERROR chat.reply req={request_id} customer={request['subject']} err=\"{outcome.reason}\""
        cut = request["text"][:PROMPT_CHARS].replace('"', "'")
        return "chat-service.log", (f"{stamp} INFO chat.reply req={request_id} customer={request['subject']} "
                                    f"model={model_short} prompt=\"{cut}...\" reply_chars={len(outcome.delivered or '')}")
    if system == "dispute-mailer":
        ticket = request["text"].split("ticket ")[1].split(":")[0]
        event = {"ts": stamp, "req": request_id, "ticket": ticket}
        event |= ({"level": "info", "event": "auto_reply_sent"} if outcome.status == "answered"
                  else {"level": "warn", "event": "auto_reply_skipped", "error": outcome.reason})
        return "mailer.jsonl", json.dumps(event)
    if system == "fraud-scorer":
        transaction = request["text"].split(":")[0].removeprefix("Payment ")
        tail = f"reason_len={len(outcome.delivered or '')}" if outcome.status == "answered" else f"error=\"{outcome.reason}\""
        return "fraud-reasons.log", f"ts={stamp} req={request_id} tx={transaction} {tail}"
    if system == "staff-search":
        when = stamp.replace("T", " ").removesuffix("Z")
        return "staff-search.log", f"[{when}] req={request_id} user={request['subject']} q=\"{request['text'][:24]}\" status={outcome.status}"
    return None


def replay(data_dir: Path, systems: dict[str, System], client, log: AuditLog, key: bytes, app_logs: Path,
           on_each=None) -> list[Outcome]:
    """Run every request through the gateway, and let each service write its own app log as it always did."""
    app_logs.mkdir(parents=True, exist_ok=True)
    for old in app_logs.glob("*"):
        old.unlink()
    outcomes = []
    for request in traffic(data_dir):
        outcome = governed_call(request, systems, client, log, key)
        line = app_log_line(request, outcome)
        if line:
            with (app_logs / line[0]).open("a") as handle:
                handle.write(line[1] + "\n")
        outcomes.append(outcome)
        if on_each:
            on_each(request, outcome)
    return outcomes
