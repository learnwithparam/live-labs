"""`agent`: list the tools, check the policy table, run one request, review them all, or open the session."""

import argparse
import json
import sys
from pathlib import Path

REVIEW = Path("out/review.json")
TIMEOUT = 90  # seconds per model call


def model():
    from openai import OpenAI

    from wrenfield.config import llm_settings

    base_url, api_key, model_name = llm_settings()
    return OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2), model_name


def show(step: dict) -> None:
    if step["kind"] == "ask":
        print(f"  ASK   {step['says']}. Policy: {step['reason']}")
    if step["kind"] == "call":
        args = ", ".join(f"{name}={value!r}" for name, value in step["args"].items() if name != "content")
        outcome = {"allow": "ran", "deny": "DENIED"}.get(step["verdict"], "approved, ran" if step["ran"] else "declined")
        if step["verdict"] == "allow" and not step["ran"]:
            outcome = "failed"
        shadow = f"  (policy would {step['policy_verdict']})" if step["policy_verdict"] != step["verdict"] else ""
        print(f"  {step['tool']}({args[:80]})  {outcome}{shadow}" + ("" if step["verdict"] == "allow" else f"  [{step['rule']}]"))
    if step["kind"] == "developer":
        print(f"  Developer: {step['text']}")


def list_tools() -> int:
    from wrenfield.tools import TOOLS

    print(f"{len(TOOLS)} tools, annotated the way MCP marks them.\n")
    for spec in TOOLS:
        hints = spec["annotations"]
        mark = "read-only" if hints["readOnlyHint"] else "destructive" if hints["destructiveHint"] else "write"
        print(f"  {spec['name']:<15}{mark:<13}{spec['description']}")
    return 0


def policy_table() -> int:
    from wrenfield import pdp
    from wrenfield.config import PDP_GRPC_PORT, PDP_HTTP_PORT
    from wrenfield.policy import Policy, check_table

    with pdp.running(PDP_HTTP_PORT, PDP_GRPC_PORT) as client:
        cells = check_table(Policy(client))
    print(f"{'':<36}{'intern':<10}{'engineer':<10}release manager")
    for start in range(0, len(cells), 3):
        row = cells[start:start + 3]
        print(f"{row[0]['case']:<36}" + "".join(f"{cell['verdict'] + ('' if cell['ok'] else '!'):<10}" for cell in row))
    matched = sum(cell["ok"] for cell in cells)
    print(f"\nCerbos agrees with the policy table on {matched} of {len(cells)} cells.")
    return 0 if matched == len(cells) else 1


def one_task(task_id: str, mode: str) -> int:
    from wrenfield import pdp
    from wrenfield.config import PDP_GRPC_PORT, PDP_HTTP_PORT
    from wrenfield.policy import Policy, PromptOnly
    from wrenfield.review import MODES, run_task, task

    chosen = task(task_id)
    client, model_name = model()
    print(f"{chosen['id']} as {chosen['who']}, {MODES[mode]}. Asks: {chosen['ask']}\n")
    with pdp.running(PDP_HTTP_PORT, PDP_GRPC_PORT) as cerbos:
        policy = Policy(cerbos) if mode == "policy" else PromptOnly(Policy(cerbos))
        row = run_task(mode, chosen, policy, client, model_name, show)
    print(f"\nAgent: {row['reply'].strip()}\n")
    print(f"Destructive calls run without approval: {row['unapproved_destructive']}. "
          f"Calls the policy forbids that ran: {row['forbidden_ran']}. "
          f"Reads blocked: {row['reads_blocked']}. Protected things lost: {', '.join(row['broken']) or 'none'}.")
    return 0


def review_all() -> int:
    from wrenfield import pdp
    from wrenfield.config import PDP_GRPC_PORT, PDP_HTTP_PORT
    from wrenfield.policy import Policy, PromptOnly
    from wrenfield.review import MODES, review, tasks

    client, model_name = model()
    print(f"{len(tasks())} developer requests, each in a fresh repo, in both modes.\n")
    with pdp.running(PDP_HTTP_PORT, PDP_GRPC_PORT) as cerbos:
        results = review({"prompt": PromptOnly(Policy(cerbos)), "policy": Policy(cerbos)}, client, model_name, lambda row: print(
            f"  {MODES[row['mode']]:<17}{row['task']}  unapproved destructive {row['unapproved_destructive']}  "
            f"forbidden ran {row['forbidden_ran']}  "
            f"asked {len(row['asked'])}  denied {row['denied']}  lost {', '.join(row['broken']) or '-'}"))
    print(f"\n{'':<18}{'unapproved destructive':<24}{'forbidden ran':<15}{'reads blocked':<15}{'asked a person':<16}{'denied':<8}lost")
    for mode, result in results.items():
        totals = result["score"]
        print(f"{MODES[mode]:<18}{totals['unapproved_destructive']:<24}{totals['forbidden_ran']:<15}"
              f"{str(totals['reads_blocked']) + ' of ' + str(totals['reads']):<15}"
              f"{totals['asked']:<16}{totals['denied']:<8}{totals['broken']}")
    REVIEW.parent.mkdir(exist_ok=True)
    REVIEW.write_text(json.dumps(results, indent=1))
    print(f"\nEvery request, call and verdict: {REVIEW}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tools")
    sub.add_parser("policy")
    one = sub.add_parser("ask")
    one.add_argument("task", help="a task id, WF-01 to WF-19")
    one.add_argument("--mode", choices=["prompt", "policy"], default="policy")
    sub.add_parser("review")
    sub.add_parser("serve").add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    if args.command == "tools":
        sys.exit(list_tools())
    if args.command == "policy":
        sys.exit(policy_table())
    if args.command == "ask":
        sys.exit(one_task(args.task, args.mode))
    if args.command == "review":
        sys.exit(review_all())
    from wrenfield.serve import main as serve

    sys.exit(serve(args.port))
