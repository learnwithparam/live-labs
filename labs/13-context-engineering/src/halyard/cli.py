"""`audit`: compare the two contexts with no key, run one audit, review both policies, or open the run console."""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

REVIEW = Path("out/review.json")
RUN = Path("out/run.json")
TIMEOUT = 60  # seconds per model call


def model():
    from openai import OpenAI

    from halyard.config import llm_settings

    base_url, api_key, model_name = llm_settings()
    return OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2), model_name


def show(step: dict) -> None:
    calls = ", ".join(f"{call['tool']} {call['args'].get('invoice', '')}".strip() for call in step["calls"]) or "reply"
    share = step["cached_tokens"] / step["prompt_tokens"] if step["prompt_tokens"] else 0
    print(f"  step {step['step']:>2}  {step['prompt_tokens']:>6,} in  {step['cached_tokens']:>6,} cached ({share:>4.0%})"
          f"  ${step['cost']:.5f}  {calls:<26}{step['changed'] or 'appended only'}")


def compare() -> int:
    """Build the first eight requests of each policy offline and show where each stops matching the last."""
    from halyard.agent import TOOLS
    from halyard.context import POLICIES, build, first_change

    turns = [[{"role": "user", "content": "Audit every carrier invoice for September 2026."}]]
    later = [{"role": "assistant", "content": None, "tool_calls": [{"id": f"call{index}", "type": "function",
              "function": {"name": "read_invoice", "arguments": json.dumps({"invoice": f"INV-0{index}"})}}]} for index in range(1, 9)]
    start = datetime(2026, 10, 1, 9, 0, 0)
    for policy, rules in POLICIES.items():
        print(f"{policy}:")
        previous = None
        for step in range(8):
            request = build(rules, TOOLS, turns, step, 16, start + timedelta(seconds=7 * step))
            changed = first_change(previous, request)
            print(f"  step {step + 1}  {len(request['messages']):>2} messages  {changed or 'appended only'}")
            previous = request
            turns = turns + [[later[step], {"role": "tool", "tool_call_id": f"call{step + 1}", "content": "(invoice text)"}]]
        turns = turns[:1]
    return 0


def one_audit(policy: str, breaks: list[str]) -> int:
    from halyard.agent import audit
    from halyard.context import POLICIES, with_breaks
    from halyard.review import score

    rules = with_breaks(POLICIES[policy], breaks)
    client, model_name = model()
    print(f"Auditing 16 invoices with the {policy} context{' + ' + ', '.join(breaks) if breaks else ''}. Model {model_name}.\n")
    run = audit(rules, client, model_name, show)
    result = score(run)
    RUN.parent.mkdir(exist_ok=True)
    RUN.write_text(json.dumps({"model": model_name, "policy": policy, "breaks": breaks, "score": result, **run}, indent=1))
    print(f"\n{result['right']} of {result['invoices']} findings right, {'complete' if result['complete'] else 'NOT complete'}. "
          f"{result['steps']} steps, cache hit rate {result['hit_rate']:.0%}, ${result['cost']:.4f} "
          f"({'billed' if result['billed'] else 'list price'}).")
    for row in result["rows"]:
        if not row["right"]:
            got = row["got"] or {"verdict": "no finding", "overcharge": 0, "reason": ""}
            print(f"  {row['invoice']}: contract says {row['want']['verdict']} {row['want']['overcharge']:.2f}, "
                  f"agent said {got['verdict']} {got['overcharge']:.2f}. {got['reason']}")
    print(f"Every step and finding: {RUN}")
    return 0


def review_all() -> int:
    from halyard.agent import audit
    from halyard.context import POLICIES
    from halyard.review import disputes, score

    client, model_name = model()
    results = {}
    for policy, rules in POLICIES.items():
        print(f"{policy} context, model {model_name}:")
        run = audit(rules, client, model_name, show)
        results[policy] = {"score": score(run), "steps": run["steps"], "disputes": disputes(run), "summary": run["summary"]}
        print()
    print(f"{'':<12}{'right':<10}{'complete':<10}{'steps':<7}{'prompt tokens':<15}{'cache hit':<11}{'cost':<10}per completed audit")
    for policy, result in results.items():
        totals = result["score"]
        per = f"${totals['cost_per_completed']:.4f}" if totals["complete"] else "none completed"
        print(f"{policy:<12}{totals['right']} of 16{'':<3}{'yes' if totals['complete'] else 'no':<10}{totals['steps']:<7}"
              f"{totals['prompt_tokens']:<15,}{totals['hit_rate']:<11.0%}${totals['cost']:<9.4f}{per}")
    REVIEW.parent.mkdir(exist_ok=True)
    REVIEW.write_text(json.dumps({"model": model_name, **results}, indent=1))
    print(f"\nEvery step, finding and bill: {REVIEW}")
    return 0


def main() -> None:
    from halyard.context import BREAKS

    parser = argparse.ArgumentParser(prog="audit")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("compare", help="where each context stops matching the last request; no key needed")
    run = sub.add_parser("run")
    run.add_argument("--context", choices=["naive", "engineered"], default="engineered")
    run.add_argument("--break", dest="breaks", action="append", choices=BREAKS, default=[],
                     help="put one naive habit back into the engineered context")
    sub.add_parser("review")
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "compare":
        sys.exit(compare())
    if args.command == "run":
        sys.exit(one_audit(args.context, args.breaks))
    if args.command == "review":
        sys.exit(review_all())
    from halyard.serve import main as serve

    sys.exit(serve(args.port))
