"""`gateway`: make the team keys, score the quality set through the proxy, send one call, or open the console."""

import argparse
import sys
import uuid

TIMEOUT = 120  # seconds per call through the proxy


def clients(proxy_url: str):
    """An OpenAI client per team, each with that team's gateway key, the way each app is configured."""
    from openai import OpenAI

    from fernway.proxy import ensure_keys

    keys = ensure_keys()
    return lambda team: OpenAI(base_url=proxy_url, api_key=keys[team], timeout=TIMEOUT, max_retries=0)


def show_keys() -> int:
    from fernway.budgets import Budgets
    from fernway.proxy import ensure_keys, write_config

    keys, budgets, path = ensure_keys(), Budgets(), write_config()
    print(f"Fernway LLM gateway: {len(keys)} team keys, config in {path}\n")
    print(f"{'team':<15}{'key':<30}{'spent':>12}{'budget':>10}  calls for")
    for team, key in keys.items():
        spent, budget = budgets.spent(team), budgets.budget(team)
        flag = "  OVER BUDGET" if spent >= budget else ""
        print(f"{team:<15}{key[:22] + '...':<30}{spent:>12,.2f}{budget:>10,.2f}  "
              f"{budgets.teams[team]['calls_for']}{flag}")
    print("\nTiers: strong, cheap, strong-backup. Fallbacks: cheap -> strong, strong -> strong-backup.")
    return 0


def run_eval(mode: str, only: list[str] | None) -> int:
    from fernway.config import llm_settings
    from fernway.evaluate import ALLOWED_DROP, compare, load, run_mode
    from fernway.proxy import running_proxy

    llm_settings()
    with running_proxy() as proxy_url:
        summary = run_mode(mode, clients(proxy_url), only=only)
    print(f"\n{'item':<9}{'team':<15}{'tier':<15}{'grade':<6}{'cost':>11}  why this tier")
    for result in summary["results"]:
        verdict = "pass" if result["passed"] else "FAIL"
        why = result["reason"] or ("asked for the strong tier" if mode == "strong" else f"asked for {mode}")
        print(f"{result['id']:<9}{result['team']:<15}{str(result['tier']):<15}{verdict:<6}"
              f"{result['cost']:>11.6f}  {'FLAGGED ' if result['flagged'] else ''}{why}")
        if not result["passed"]:
            print(f"{'':<9}wrong: {'; '.join(result['wrong']) or result['error']}")
    print(f"\n{mode}: {summary['passed']} of {summary['requests']} passed. Cost ${summary['cost']:.6f}, "
          f"${summary['cost_per_request']:.7f} per request. Tiers: {summary['tiers']}. "
          f"Flagged: {summary['flagged']}. Every cost billed by OpenRouter: {'yes' if summary['all_billed'] else 'NO'}.")
    naive, routed = load("strong"), load("routed")
    if mode == "routed" and naive:
        metric = compare(naive, routed)
        print(f"\nRouted vs all strong: {metric['routed_passed']} vs {metric['naive_passed']} passed "
              f"(allowed drop {ALLOWED_DROP}), ${metric['routed_cost_per_request']:.7f} vs "
              f"${metric['naive_cost_per_request']:.7f} per request, {metric['saving']:.0%} cheaper. "
              f"Quality holds: {'yes' if metric['quality_holds'] else 'NO'}.")
    return 0


def ask(team: str, text: str, cheap_down: bool) -> int:
    from fernway.budgets import Ledger
    from fernway.config import llm_settings
    from fernway.proxy import running_proxy

    llm_settings()
    run = f"ask-{uuid.uuid4().hex[:6]}"
    with running_proxy(cheap_down=cheap_down) as proxy_url:
        client = clients(proxy_url)(team)
        try:
            response = client.chat.completions.create(
                model="auto", messages=[{"role": "user", "content": text}],
                extra_body={"metadata": {"item": "ask", "run": run}})
            print(f"\nReply: {response.choices[0].message.content}")
        except Exception as error:
            print(f"\nRefused ({getattr(error, 'status_code', 'error')}): "
                  f"{getattr(error, 'body', None) and error.body.get('message') or error}")
        from fernway.evaluate import wait_for_ledger

        rows = wait_for_ledger(run, 1, Ledger())
    print("\nWhat the gateway booked:")
    for row in rows:
        detail = row.get("reason") or row.get("error") or ""
        moved = f", fell back from {row['fallback_from']} to {row['tier']}" if row.get("fallback_from") else ""
        print(f"  {row['purpose']:<9}{row['status']:<15}{str(row.get('tier') or '-'):<15}"
              f"${row.get('cost') or 0:.6f}{moved}  {detail[:160]}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("keys", help="make each team's gateway key and write the proxy config; no model key needed")
    scoring = sub.add_parser("eval", help="send the quality set through the proxy and grade it")
    scoring.add_argument("--mode", choices=["strong", "routed", "cheap"], default="routed")
    scoring.add_argument("--only", help="comma separated item ids, such as QA-01,CARE-05")
    one = sub.add_parser("ask", help="send one call as a team, with model auto")
    one.add_argument("team")
    one.add_argument("text")
    one.add_argument("--cheap-down", action="store_true", help="point the cheap tier at a dead address")
    from fernway.config import PAGE_PORT

    sub.add_parser("serve", help="the gateway admin console").add_argument("--port", type=int, default=PAGE_PORT)
    args = parser.parse_args()
    if args.command == "keys":
        sys.exit(show_keys())
    if args.command == "eval":
        sys.exit(run_eval(args.mode, args.only.split(",") if args.only else None))
    if args.command == "ask":
        sys.exit(ask(args.team, args.text, args.cheap_down))
    from fernway.serve import main as serve

    sys.exit(serve(args.port))
