"""`brickhaven-api serve|load|reconcile`: run the service, load it, and check its ledger against billing."""

import argparse
import sys

from brickhaven_api.config import API_PORT, OUT, llm_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="brickhaven-api")
    commands = parser.add_subparsers(dest="command", required=True)
    serving = commands.add_parser("serve", help="run the API and the operator portal")
    serving.add_argument("--naive", action="store_true", help="no key, no limit, no fallback")
    serving.add_argument("--break-primary", action="store_true", help="make the primary model fail, to see fallback")
    serving.add_argument("--port", type=int, default=API_PORT, help="API port; the portal uses the next one")
    loading = commands.add_parser("load", help="send a burst of calls and report the numbers")
    loading.add_argument("--port", type=int, default=API_PORT)
    loading.add_argument("--requests", type=int, default=60)
    loading.add_argument("--concurrency", type=int, default=12)
    loading.add_argument("--timeout", type=float, default=120, help="hard deadline in seconds for the whole run")
    checking = commands.add_parser("reconcile", help="check the ledger against the provider's billing records")
    checking.add_argument("--mode", choices=["hardened", "naive"], default="hardened")
    args = parser.parse_args()
    if args.command == "serve":
        from brickhaven_api.serve import run
        run(args.naive, args.break_primary, args.port)
    elif args.command == "load":
        from brickhaven_api.loadtest import run_load
        report = run_load(f"http://127.0.0.1:{args.port}", f"http://127.0.0.1:{args.port + 1}", args.requests, args.concurrency, args.timeout)
        print("\n".join(report.summary_lines()))
    else:
        from brickhaven_api.billing import OpenRouterBilling
        from brickhaven_api.ledger import Ledger, reconcile
        rows = Ledger(OUT / f"ledger-{args.mode}.jsonl").rows
        base_url, api_key = llm_settings()
        result = reconcile(rows, OpenRouterBilling(base_url, api_key).lookup)
        print(f"checked {result.checked} generations, {len(result.missing)} missing")
        print(f"ledger ${result.ledger_total:.6f}, provider billing ${result.billing_total:.6f}, gap {result.gap_share * 100:.2f}%")
        print(f"ledger within tolerance: {'yes' if result.within_tolerance else 'no'}")
        sys.exit(0 if result.within_tolerance else 1)
