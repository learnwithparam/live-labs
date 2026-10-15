"""The audit desk: answer an auditor's request from the inventory and the log, with the evidence attached.

Every answer is a query, not a memory. The inventory says what each system is and who owns it; the log
says what each one did. An answer carries the records it rests on, so the auditor can check the chain
under each one instead of taking the bank's word.
"""

import json
import time
from pathlib import Path

from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as gen_ai

from larkspur.auditlog import AuditLog, pseudonym, verify
from larkspur.inventory import System


def requests_of(data_dir: Path) -> list[dict]:
    return json.loads((data_dir / "audit_requests.json").read_text())


def text_of(messages: list[dict] | None) -> str:
    return " ".join(part["content"] for message in messages or [] for part in message["parts"])


def evidence_row(record: dict) -> dict:
    """One record as the auditor reads it: who, when, which system and version, what went in and out."""
    return {"seq": record["seq"], "request": record[gen_ai.GEN_AI_CONVERSATION_ID], "time": record["time"],
            "system": record[gen_ai.GEN_AI_AGENT_ID], "version": record.get(gen_ai.GEN_AI_AGENT_VERSION),
            "prompt": record.get(gen_ai.GEN_AI_PROMPT_NAME), "prompt_sha256": record.get("prompt.sha256"),
            "model": record.get(gen_ai.GEN_AI_REQUEST_MODEL), "response_id": record.get(gen_ai.GEN_AI_RESPONSE_ID),
            "outcome": record["outcome"], "reason": record.get("refusal.reason") or record.get("error.type"),
            "subject": record["enduser.pseudo.id"], "input": text_of(record[gen_ai.GEN_AI_INPUT_MESSAGES]),
            "reply": text_of(record.get(gen_ai.GEN_AI_OUTPUT_MESSAGES)), "delivered": record.get("delivered.text"),
            "disclosure_shown": record.get("disclosure.shown"), "prev_hash": record["prev_hash"],
            "record_hash": record["record_hash"]}


class Desk:
    """Answers each kind of request. The auditor's words map to a kind and its parameters in the labelled set."""

    def __init__(self, systems: dict[str, System], log: AuditLog, key: bytes, anchor: dict | None = None):
        self.systems, self.log, self.key, self.anchor = systems, log, key, anchor

    def between(self, start: str, end: str, where: str = "1", params: tuple = ()) -> list[dict]:
        return self.log.query(f"substr(at, 1, 10) BETWEEN ? AND ? AND {where}", (start, end, *params))

    def answer(self, request: dict) -> dict:
        started = time.perf_counter()
        result, systems, records = getattr(self, request["kind"])(**request["params"])
        return {"id": request["id"], "result": result, "systems": [self.systems[name].card() for name in systems],
                "records": [evidence_row(record) for record in records],
                "seconds": round(time.perf_counter() - started, 4)}

    def customer_facing(self):
        facing = sorted(system.id for system in self.systems.values() if system.talks_to_customers)
        return {"systems": facing}, facing, []

    def disclosure_status(self):
        facing = [system for system in self.systems.values() if system.talks_to_customers]
        names = [system.id for system in facing]
        shown = [record for record in self.log.query("outcome = 'answered'") if record[gen_ai.GEN_AI_AGENT_ID] in names]
        return ({"discloses": sorted(system.id for system in facing if system.disclosure),
                 "missing": sorted(system.id for system in facing if not system.disclosure)}, names, shown)

    def conversation(self, system: str, subject: str, day: str):
        records = self.between(day, day, "system_id = ? AND subject = ?", (system, pseudonym(subject, self.key)))
        return {"requests": [record[gen_ai.GEN_AI_CONVERSATION_ID] for record in records]}, [system], records

    def provenance(self, system: str, subject: str, day: str):
        _, _, records = self.conversation(system, subject, day)
        answered = [record for record in records if record["outcome"] == "answered"]
        return ({"models": sorted({record[gen_ai.GEN_AI_RESPONSE_MODEL] for record in answered}),
                 "versions": sorted({record[gen_ai.GEN_AI_PROMPT_NAME] for record in answered})}, [system], answered)

    def subject_history(self, subject: str, start: str, end: str):
        records = self.between(start, end, "subject = ?", (pseudonym(subject, self.key),))
        touched = sorted({record[gen_ai.GEN_AI_AGENT_ID] for record in records} & set(self.systems))
        return {"requests": [record[gen_ai.GEN_AI_CONVERSATION_ID] for record in records]}, touched, records

    def system_card(self, system: str):
        card = self.systems[system]
        return {"owner": card.owner, "models": card.models, "talks_to_customers": card.talks_to_customers}, [system], []

    def unregistered(self):
        # Refusals are logged on purpose: the only trace of shadow AI is the call the gateway turned away.
        records = [record for record in self.log.query() if record[gen_ai.GEN_AI_AGENT_ID] not in self.systems]
        return self.split(records, {"systems": sorted({record[gen_ai.GEN_AI_AGENT_ID] for record in records})}), [], records

    def unlisted_models(self):
        records = [record for record in self.log.query() if record[gen_ai.GEN_AI_AGENT_ID] in self.systems
                   and record.get(gen_ai.GEN_AI_REQUEST_MODEL)
                   and record[gen_ai.GEN_AI_REQUEST_MODEL] not in self.systems[record[gen_ai.GEN_AI_AGENT_ID]].models]
        touched = sorted({record[gen_ai.GEN_AI_AGENT_ID] for record in records})
        return self.split(records, {"models": sorted({record[gen_ai.GEN_AI_REQUEST_MODEL] for record in records})}), touched, records

    def count(self, system: str, start: str, end: str):
        records = self.between(start, end, "system_id = ? AND outcome = 'answered'", (system,))
        return {"count": len(records)}, [system], records

    def undisclosed(self):
        facing = {system.id for system in self.systems.values() if system.talks_to_customers}
        records = [record for record in self.log.query("outcome = 'answered'")
                   if (record.get("talks_to_customers") or record[gen_ai.GEN_AI_AGENT_ID] in facing)
                   and record.get("disclosure.shown") is not True]
        return {"requests": [record[gen_ai.GEN_AI_CONVERSATION_ID] for record in records]}, sorted(facing), records

    def system_activity(self, system: str, start: str, end: str):
        records = self.between(start, end, "system_id = ?", (system,))
        return self.split(records, {}), [system], records

    def integrity(self):
        status = verify(self.log.path, self.anchor)
        return {"verified": status["ok"], "records": status["records"], "head": status["head"],
                "problem": status["problem"]}, [], []

    @staticmethod
    def split(records: list[dict], result: dict) -> dict:
        def ids(outcome: str) -> list[str]:
            return [record[gen_ai.GEN_AI_CONVERSATION_ID] for record in records if record["outcome"] == outcome]
        return {**result, "answered": ids("answered"), "refused": ids("refused")}


def grade(answer: dict, expected: dict, chain_ok: bool) -> dict:
    """Correct means every labelled field matches. Complete means the evidence would satisfy an auditor:
    the chain under it verifies, and each answered record shows the text the person actually saw."""
    wrong = [key for key, value in expected.items() if answer["result"].get(key) != value]
    missing_text = [row["request"] for row in answer["records"] if row["outcome"] == "answered" and not row["reply"]]
    complete = chain_ok and not missing_text and not answer["result"].get("cannot")
    return {"correct": not wrong, "complete": complete, "wrong_fields": wrong, "missing_text": missing_text}


def answer_all(desk: Desk, requests: list[dict]) -> dict:
    """Every labelled request, answered and graded. The metric is correct-and-complete over all of them."""
    chain = verify(desk.log.path, desk.anchor)
    rows = []
    for request in requests:
        answer = desk.answer(request)
        rows.append({**answer, "asked": request["asked"], "kind": request["kind"], "expected": request["expected"],
                     "grade": grade(answer, request["expected"], chain["ok"])})
    good = sum(row["grade"]["correct"] and row["grade"]["complete"] for row in rows)
    seconds = sorted(row["seconds"] for row in rows)
    return {"rows": rows, "chain": chain, "answered": good, "total": len(rows),
            "median_seconds": seconds[len(seconds) // 2], "slowest_seconds": seconds[-1]}
