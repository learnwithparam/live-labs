"""`gpu`: rehearse the GPU run with the stand-in, run the hosted model, read the report, review the queue,
or open the endpoint console."""

import argparse
import json
import sys
from pathlib import Path

from corvane_gpu.config import DECISIONS, GPU_RUN, HOSTED_RUN, OUT, PAGE_PORT, RECORDED_HOSTED_RUN, load_dotenv


def money(amount: float | None, places: int = 4) -> str:
    return "n/a" if amount is None else f"${amount:,.{places}f}"


def seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f} s"


def load_hosted() -> dict | None:
    """This machine's own hosted run if there is one, else the run recorded with the lab."""
    if HOSTED_RUN.exists():
        return json.loads(HOSTED_RUN.read_text())
    if RECORDED_HOSTED_RUN.exists():
        return {**json.loads(RECORDED_HOSTED_RUN.read_text()), "recorded": True}
    return None


def current_report(run_path: Path = GPU_RUN) -> dict:
    from corvane_gpu.golden import golden_set
    from corvane_gpu.report import build_report
    from corvane_gpu.review import decisions

    return build_report(json.loads(run_path.read_text()), load_hosted(), golden_set(), decisions(DECISIONS))


def print_waves(report: dict) -> None:
    about = report["about"]
    print(f"Served: {about['model']} on {about.get('gpu')} ({about['where']}), vLLM {about.get('vllm', 'n/a')}.")
    auth = report["auth"]
    if auth:
        print(f"Auth: no key {auth['no_key']}, wrong key {auth['wrong_key']}, right key {auth['right_key']}.")
    for wave in report["waves"]:
        if not wave["started"]:
            print(f"  {wave['name']}: {wave['reason']}")
            continue
        latency, billing = wave.get("latency") or {}, wave.get("billing") or {}
        print(f"  {wave['name']}: {wave['items']} items, cold start {seconds(wave.get('cold_start_seconds'))}, "
              f"p50 {seconds(latency.get('p50_seconds'))}, p95 {seconds(latency.get('p95_seconds'))}, "
              f"{latency.get('failed', 0)} failed ({latency.get('timed_out', 0)} timed out), "
              f"{latency.get('tokens_per_second')} tokens/s.")
        print(f"    billed {billing.get('billed_seconds')} s = cold {billing.get('cold_start_seconds')} + busy "
              f"{billing.get('busy_seconds')} + idle {billing.get('idle_seconds')}, {money(billing.get('cost'))}.")
    if report["guard_hit"]:
        print("The time guard ended the run early. Every unsent item counts as failed.")


def print_report(report: dict) -> None:
    print_waves(report)
    golden = next(wave for wave in report["waves"] if wave["name"] == "golden set")
    hosted, economics = report["hosted"], report["economics"]
    if golden.get("accuracy"):
        print(f"\nGolden set, served: {golden['accuracy']['correct']}/{golden['accuracy']['total']} right "
              f"({golden['accuracy']['accuracy']:.1%}).")
    if hosted:
        note = " (the run recorded with the lab)" if hosted["recorded"] else ""
        print(f"Golden set, hosted {hosted['model']}{note}: {hosted['accuracy']['correct']}/{hosted['accuracy']['total']} "
              f"right ({hosted['accuracy']['accuracy']:.1%}), p95 {seconds(hosted['latency']['p95_seconds'])}.")
    if economics:
        print(f"Cost per 1,000 items: served {money(economics['gpu_per_thousand'])}, hosted "
              f"{money(economics['hosted_per_thousand'])}.")
        break_even = economics["break_even_items"]
        print(f"Break-even: {'never' if break_even is None else f'{break_even:,} items'} per wake-up "
              f"({economics['fixed_seconds_per_wake']} s fixed per wake, {economics['gpu_seconds_per_item']} s per item).")
    print(f"Price: {report['price']['source']}.")
    waiting = [item for item in report["queue"] if not item["decision"]]
    print(f"\nReview queue: {len(report['queue'])} items, {len(waiting)} waiting for a reviewer.")
    for item in report["queue"]:
        decided = item["decision"]
        outcome = f"{decided['category']} by {decided['reviewer']}" if decided else "waiting"
        print(f"  {item['id']}  {item['descriptor'][:34]:<34} {item['reason'][:60]:<60} {outcome}")


def rehearse() -> int:
    from corvane_gpu.gpu_run import rehearse as rehearse_run
    from corvane_gpu.golden import DATA

    out_dir = OUT / "rehearsal"
    results = rehearse_run(DATA, out_dir, PAGE_PORT + 1, hang_on="FIGMA", ramble_on="MARRIOTT")
    (out_dir / "results.json").write_text(json.dumps(results, indent=1))
    print_waves(current_report(out_dir / "results.json"))
    return 0


def hosted() -> int:
    from corvane_gpu.config import llm_settings
    from corvane_gpu.golden import EXAMPLES, golden_set, read_jsonl
    from corvane_gpu.hosted import run_hosted, save

    base_url, key, model = llm_settings()
    run = run_hosted(golden_set(), read_jsonl(EXAMPLES), base_url, key, model)
    save(run, HOSTED_RUN)
    failed = sum(outcome["status"] != "ok" for outcome in run["outcomes"])
    print(f"{model}: {len(run['outcomes'])} items in {run['run_seconds']} s, {failed} failed, "
          f"billed {money(run['total_cost'])}. Saved to {HOSTED_RUN}.")
    return 0


def review(item_id: str, category: str, reviewer: str) -> int:
    from corvane_gpu.review import decide

    report = current_report()
    try:
        decision = decide(DECISIONS, {item["id"] for item in report["queue"]}, item_id, category, reviewer)
    except ValueError as error:
        print(error)
        return 1
    print(f"{item_id} set to {decision['category']} by {decision['reviewer']}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="gpu", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rehearse", help="the whole GPU run on this laptop with the stand-in server")
    commands.add_parser("hosted", help="the golden set through the hosted model")
    report_parser = commands.add_parser("report", help="the GPU run against the hosted run")
    report_parser.add_argument("--run", type=Path, default=GPU_RUN)
    review_parser = commands.add_parser("review", help="decide an item in the review queue")
    review_parser.add_argument("item_id")
    review_parser.add_argument("category")
    review_parser.add_argument("--reviewer", default="Dana Okafor, finance ops")
    commands.add_parser("serve", help=f"the endpoint console on http://127.0.0.1:{PAGE_PORT}")
    args = parser.parse_args(argv)
    if args.command == "rehearse":
        return rehearse()
    if args.command == "hosted":
        return hosted()
    if args.command == "report":
        print_report(current_report(args.run))
        return 0
    if args.command == "review":
        return review(args.item_id, args.category, args.reviewer)
    from corvane_gpu.serve import serve

    serve(PAGE_PORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
