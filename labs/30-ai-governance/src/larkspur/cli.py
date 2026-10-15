"""`govern`: check the inventory, replay the fortnight, answer the auditor, verify the chain, sign the pack."""

import argparse
import json
import sys

from larkspur.config import ANSWER_PACK, APP_LOGS, AUDIT_DB, DATA, DECISIONS, OUT

TIMEOUT = 90  # seconds per model call
ANSWERS = OUT / "answers.json"
TAMPERED = OUT / "audit-tampered.sqlite"


def model_client():
    from openai import OpenAI

    from larkspur.config import llm_settings

    base_url, api_key = llm_settings()
    return OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2)


def anchor() -> dict | None:
    """The head the officer signed, if a pack exists. Verify checks the log still holds it."""
    from larkspur.signoff import read_json

    return read_json(ANSWER_PACK, {}).get("anchor")


def open_desk():
    from larkspur import inventory
    from larkspur.audit import Desk
    from larkspur.auditlog import AuditLog
    from larkspur.config import pseudonym_key

    if not AUDIT_DB.exists():
        raise SystemExit("No audit log yet. Run `uv run govern replay` first.")
    systems = inventory.load()
    return systems, Desk(systems, AuditLog(AUDIT_DB), pseudonym_key(), anchor())


def show_inventory() -> int:
    from larkspur import inventory

    try:
        systems = inventory.load()
    except inventory.InventoryError as error:
        print(f"Inventory rejected: {error}")
        return 1
    print(f"Inventory valid: {len(systems)} systems.\n")
    for system in systems.values():
        facing = "talks to customers" if system.talks_to_customers else "internal"
        print(f"  {system.id:<15}{facing:<20}{system.owner:<38}{', '.join(system.models)}")
    found = inventory.findings(systems)
    print(f"\nFindings for compliance: {len(found)}")
    for finding in found:
        print(f"  BLOCKED {finding['reason']}\n  Owner proposes: {finding['proposed_disclosure'] or 'nothing yet'}")
    return 0


def replay() -> int:
    from larkspur import inventory
    from larkspur.auditlog import AuditLog
    from larkspur.config import pseudonym_key
    from larkspur.services import replay as run_traffic

    systems, client = inventory.load(), model_client()
    # The lab rebuilds its fortnight from scratch. A production log is never deleted, only appended to.
    for stale in (AUDIT_DB, ANSWER_PACK, DECISIONS, ANSWERS):
        stale.unlink(missing_ok=True)
    log = AuditLog(AUDIT_DB)

    def report(request, outcome):
        detail = outcome.reason if outcome.reason else f"{len(outcome.delivered)} chars, disclosure shown: {outcome.record['disclosure.shown']}"
        print(f"  {request['id']}  {request['at'][:10]}  {request['system']:<15}{outcome.status.upper():<10}{detail}")

    print("Replaying 1 to 12 September 2026 through the gateway.\n")
    outcomes = run_traffic(DATA, systems, client, log, pseudonym_key(), APP_LOGS, report)
    log.close()
    tally = {status: sum(outcome.status == status for outcome in outcomes) for status in ("answered", "refused", "failed")}
    cost = sum(outcome.record.get("cost.usd", 0.0) for outcome in outcomes)
    print(f"\n{len(outcomes)} calls: {tally['answered']} answered, {tally['refused']} refused, {tally['failed']} failed. "
          f"Cost ${cost:.4f}. Audit records written: {len(outcomes)}.")
    return 1 if tally["failed"] else 0


def show_answer(row: dict, naive_row: dict) -> None:
    mark = "ANSWERED" if row["grade"]["correct"] and row["grade"]["complete"] else "NOT ANSWERED"
    print(f"{row['id']}  {row['asked']}\n  {mark} in {row['seconds'] * 1000:.1f} ms: {json.dumps(row['result'])}")
    for system in row["systems"]:
        print(f"  inventory  {system['id']}: {system['owner']}, talks to customers: {system['talks_to_customers']}")
    for record in row["records"][:6]:
        print(f"  record {record['seq']:>3}  {record['request']}  {record['time']}  {record['system']}  {record['outcome']}"
              f"  {record['prompt'] or ''}  hash {record['record_hash'][:12]}")
        if record["delivered"]:
            print("    shown: " + record["delivered"].replace("\n", " ")[:160])
    steps = len(naive_row["steps"])
    print(f"  By grepping app logs: {'answered' if naive_row['complete'] else 'not answered'} after "
          f"{steps} step{'' if steps == 1 else 's'}. {naive_row['why'] or ''}\n")


def answer(request_id: str | None) -> int:
    from larkspur import naive
    from larkspur.audit import answer_all, requests_of

    _, desk = open_desk()
    requests = [request for request in requests_of(DATA) if request_id in (None, request["id"])]
    if not requests:
        raise SystemExit(f"No audit request {request_id}")
    governed, grepped = answer_all(desk, requests), naive.answer_all(APP_LOGS, requests)
    for row, naive_row in zip(governed["rows"], grepped["rows"]):
        show_answer(row, naive_row)
    if request_id:
        return 0
    ANSWERS.write_text(json.dumps({"governed": governed, "naive": grepped}, indent=1))
    print(f"From the inventory and the audit log: {governed['answered']} of {governed['total']} answered correctly and "
          f"completely, median {governed['median_seconds'] * 1000:.1f} ms, slowest {governed['slowest_seconds'] * 1000:.1f} ms.")
    print(f"By grepping app logs: {grepped['answered']} of {grepped['total']}, after {grepped['steps']} steps and "
          f"{grepped['lines_read']} lines read by eye.")
    print(f"Chain: {'verified' if governed['chain']['ok'] else 'BROKEN'} over {governed['chain']['records']} records.")
    return 0 if governed["answered"] == governed["total"] else 1


def verify_log(path) -> int:
    from larkspur.auditlog import verify

    status = verify(path, anchor() if path == AUDIT_DB else None)
    if status["ok"]:
        print(f"Chain verified: {status['records']} records, head {status['head']['seq']} {status['head']['hash'][:16]}")
        return 0
    print(f"Chain BROKEN at record {status['broken_at']}: {status['problem']}")
    return 1


def tamper(mode: str, seq: int) -> int:
    from larkspur.tamper import tampered_copy

    print(f"On a copy of the log, an insider {tampered_copy(AUDIT_DB, TAMPERED, mode, seq)}.")
    return 0 if verify_log(TAMPERED) == 1 else 1


def decide(system: str, decision: str, by: str) -> int:
    from larkspur import inventory, signoff

    signoff.decide(DECISIONS, system, decision, by, inventory.findings(inventory.load()))
    print(f"Recorded: {by} decided {decision} on {system}. {signoff.DECISIONS[decision]}")
    return 0


def sign(by: str) -> int:
    from larkspur import inventory, signoff
    from larkspur.audit import answer_all, requests_of

    systems, desk = open_desk()
    answers = answer_all(desk, requests_of(DATA))
    try:
        pack = signoff.sign(ANSWER_PACK, answers, inventory.findings(systems), signoff.read_json(DECISIONS, {}), by)
    except ValueError as error:
        print(error)
        return 1
    print(f"Answer pack signed by {by}: {len(pack['answers'])} answers, anchored at record {pack['anchor']['seq']} "
          f"{pack['anchor']['hash'][:16]}. Written to {ANSWER_PACK}.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="govern")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory", help="validate the inventory and list findings; no key needed")
    sub.add_parser("replay", help="run the fortnight's traffic through the gateway on the real model")
    one = sub.add_parser("answer", help="answer the auditor's requests from the inventory and the log")
    one.add_argument("request", nargs="?", help="one request id, A-01 to A-14; all when left out")
    sub.add_parser("verify", help="walk the hash chain")
    broken = sub.add_parser("tamper", help="edit or delete a record in a copy of the log, then verify the copy")
    broken.add_argument("--mode", choices=["edit", "delete"], default="edit")
    broken.add_argument("--seq", type=int, default=4)
    choice = sub.add_parser("decide", help="the compliance officer's decision on a finding")
    choice.add_argument("system")
    choice.add_argument("decision", choices=["approve-disclosure", "keep-blocked"])
    choice.add_argument("--by", required=True)
    sub.add_parser("signoff", help="sign the answer pack").add_argument("--by", required=True)
    from larkspur.config import PORT

    sub.add_parser("serve").add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    commands = {"inventory": show_inventory, "replay": replay, "answer": lambda: answer(args.request),
                "verify": lambda: verify_log(AUDIT_DB), "tamper": lambda: tamper(args.mode, args.seq),
                "decide": lambda: decide(args.system, args.decision, args.by), "signoff": lambda: sign(args.by)}
    if args.command in commands:
        sys.exit(commands[args.command]())
    from larkspur.serve import main as serve

    sys.exit(serve(args.port))
