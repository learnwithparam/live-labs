"""`nightly`: run tonight's batch as durable executions with a crash, run the plain-loop baseline, answer a
held job, or open the run console."""

import argparse
import sys

from meterline.config import HOST_PORT, OUT, PAGE_PORT, RUNNER_PORT, load_dotenv


def money(amount: float, places: int = 2) -> str:
    return f"${amount:,.{places}f}"


def print_night(report: dict) -> None:
    metrics, bill, crash = report["metrics"], report["bill"], report["crash"]
    if crash:
        print(f"Crash: host process {crash['process_id']} killed with SIGKILL at {crash['at']:.1f}s, after "
              f"{report['run']['crash_after_writes']} ledger writes. New process {crash['new_process_id']} up at {crash['restarted_at']:.1f}s.")
    for lane in report["lanes"]:
        steps = ", ".join(f"{segment['step']} {segment['state']}" for segment in lane["segments"])
        marker = "  resumed after crash" if lane["resumed"] else ""
        print(f"  {lane['customer_id']}  {lane['outcome']:<9} {steps}{marker}")
        if lane["outcome"] in ("held", "stopped"):
            print(f"           why: {lane['reason']}")
    counts = metrics["outcomes"]
    print(f"\nJobs that reached an outcome after the crash: {metrics['reached_outcome']} of {metrics['jobs']} "
          f"({counts['matched']} matched, {counts['corrected']} corrected, {counts['approved']} approved, "
          f"{counts['held']} held for finance, {counts['rejected']} rejected, {counts['stopped']} stopped).")
    print(f"Ledger: {metrics['ledger_rows']} rows, {metrics['duplicate_writes']} duplicate writes, "
          f"{metrics['repeat_writes_absorbed']} repeat writes absorbed by the idempotency key.")
    print(f"Model: {metrics['model_calls']} calls, {metrics['model_calls_cut_off_and_redone']} cut off by the crash and redone, "
          f"{metrics['completed_model_steps_recalled']} completed steps called again.")
    lambda_bill = bill["lambda"]
    print(f"Cost per run: {money(bill['per_run'], 4)} = model {money(bill['model'], 4)} + Lambda {money(lambda_bill['total'], 4)} "
          f"({lambda_bill['invokes']} invokes, {lambda_bill['gb_seconds']} GB-s, {lambda_bill['operations']} durable operations).")


def run_night(crash_after: int) -> int:
    from meterline.batch import run_night as run_batch
    from meterline.config import llm_settings
    from meterline.runtime import LocalCloud

    llm_settings()
    cloud = LocalCloud(OUT, RUNNER_PORT, HOST_PORT)
    try:
        report = run_batch(cloud, crash_after)
    finally:
        cloud.stop()
    print_night(report)
    return 0 if report["metrics"]["reached_outcome"] == report["metrics"]["jobs"] else 1


def run_naive(crash_after: int) -> int:
    from meterline.config import llm_settings
    from meterline.naive import run_naive as run_loop

    llm_settings()
    report = run_loop(OUT, crash_after)
    print(f"Plain loop on one VM, killed after {crash_after} ledger writes and restarted from the top by systemd.")
    print(f"Ledger: {report['ledger_rows']} rows for {report['customers_written']} invoices, "
          f"{report['duplicate_writes']} duplicate writes ({', '.join(report['customers_billed_twice']) or 'none'}).")
    print(f"Model: {report['model_calls']} calls, {report['model_calls_repeated']} repeated after the restart, "
          f"{report['completed_model_steps_recalled']} of them for jobs that already had an answer.")
    bill = report["bill"]
    print(f"Cost per run: {money(bill['per_run'], 4)} = model {money(bill['model'], 4)} + "
          f"{bill['vm']['name']} for {bill['vm']['hours']:.0f} h {money(bill['vm']['total'], 4)}.")
    return 0


def review(customer_id: str, approved: bool, reviewer: str) -> int:
    from meterline.review import answer
    from meterline.runtime import LocalCloud

    cloud = LocalCloud(OUT, RUNNER_PORT, HOST_PORT)
    try:
        cloud.start_runner()
        cloud.start_host()
        lane = answer(cloud, customer_id, approved, reviewer)
    finally:
        cloud.stop()
    rows = lane["ledger"]
    print(f"{customer_id} {lane['outcome']} by {reviewer}. Ledger rows for {customer_id}: {len(rows)}"
          + (f", total {money(rows[0]['total'])}." if rows else "."))
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="nightly", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="tonight's batch as durable executions, with a real crash")
    run_parser.add_argument("--crash-after", type=int, default=12, help="ledger writes before the host is killed (0: no crash)")
    naive_parser = commands.add_parser("naive", help="the same job as a plain loop on an always-on VM")
    naive_parser.add_argument("--crash-after", type=int, default=12)
    review_parser = commands.add_parser("review", help="approve or reject a job held for finance")
    review_parser.add_argument("customer_id")
    review_parser.add_argument("decision", choices=["approve", "reject"])
    review_parser.add_argument("--reviewer", default="Priya Nair, finance")
    commands.add_parser("serve", help=f"the run console on http://127.0.0.1:{PAGE_PORT}")
    args = parser.parse_args(argv)
    if args.command == "run":
        return run_night(args.crash_after)
    if args.command == "naive":
        return run_naive(args.crash_after)
    if args.command == "review":
        return review(args.customer_id, args.decision == "approve", args.reviewer)
    from meterline.serve import serve

    serve(PAGE_PORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
