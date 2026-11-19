"""`brickhaven-evals run|naive|gate|label|serve`: run candidates and gate a release on a golden set."""

import argparse
import datetime
import json
import sys

from brickhaven_evals.config import RUNS, THRESHOLDS
from brickhaven_evals.extractor import load_candidate, load_run, run_candidate, save_run
from brickhaven_evals.golden import load_golden, verify_case
from brickhaven_evals.scoring import gate


def scored(name: str):
    """The verdict for one candidate against the live release's saved run."""
    golden = load_golden()
    live_run = load_run("live", RUNS)
    return gate(golden, live_run, load_run(name, RUNS))


def main() -> None:
    parser = argparse.ArgumentParser(prog="brickhaven-evals")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a candidate on every verified case and save its outputs")
    run.add_argument("candidate")
    naive = commands.add_parser("naive", help="the one-number gate")
    naive.add_argument("candidate")
    release = commands.add_parser("gate", help="the per-field gate")
    release.add_argument("candidate")
    label = commands.add_parser("label", help="a person verifies a proposed case")
    label.add_argument("case_id")
    label.add_argument("--by", required=True)
    label.add_argument("--orders", help="JSON list of work orders that replaces the proposal")
    commands.add_parser("serve", help="open the labelling bench and scorecard")
    args = parser.parse_args()
    if args.command == "serve":
        from brickhaven_evals.serve import run as serve_page
        serve_page()
    elif args.command == "run":
        cases = load_golden()
        path = save_run(args.candidate, run_candidate(load_candidate(args.candidate), cases), RUNS)
        print(f"ran {len(cases)} verified cases for {args.candidate}: {path.name}")
    elif args.command == "label":
        corrected = {"work_orders": json.loads(args.orders)} if args.orders else None
        verified = verify_case(args.case_id, args.by, datetime.date.today().isoformat(), corrected)
        print(f"verified: {verified['id']} by {verified['verified_by']}")
    elif args.command == "naive":
        verdict = scored(args.candidate)
        print(f"overall exact match: live {verdict.naive_live:.2f}, {args.candidate} {verdict.naive_candidate:.2f}")
        print(f"naive gate: {'SHIPS' if verdict.naive_ships else 'BLOCKED'}")
    else:
        verdict = scored(args.candidate)
        for name, threshold in THRESHOLDS.items():
            rate = verdict.candidate_rates[name]
            print(f"{name}: {rate:.2f} (needs {threshold:.2f}, live {verdict.live_rates[name]:.2f}) {'ok' if name not in verdict.below_threshold else 'BELOW'}")
        print(f"regressions: {len(verdict.regressions)}")
        for regression in verdict.regressions:
            print(f"  {regression.case_id} {regression.field_name}: {regression.message[:60]}")
        print(f"release: {'SHIPS' if verdict.ships else 'BLOCKED'}")
        sys.exit(0 if verdict.ships else 1)
