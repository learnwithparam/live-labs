"""`gateway`: show the approved list, check pins, prove fail-closed, run one request or all ten, open the console."""

import argparse
import asyncio
import logging
import sys

TIMEOUT = 60  # seconds per model call


def quiet() -> None:
    # The MCP SDK turns on INFO logging, which would print a line for every HTTP request.
    logging.disable(logging.INFO)


def model():
    from openai import AsyncOpenAI

    from basalt.config import llm_settings

    base_url, api_key, model_name = llm_settings()
    return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2), model_name


def show_catalogue() -> int:
    from basalt.catalogue import load

    servers = load()
    print(f"{len(servers)} approved MCP servers. Anything else is unreachable through the gateway.\n")
    for server in servers.values():
        print(f"{server.name}  owner {server.owner}, approved by {server.approved_by} on {server.approved_on}")
        for tool in server.tools.values():
            print(f"  {tool.name:<18} pin {tool.sha256[:12]}  teams: {', '.join(tool.teams)}")
    return 0


def check(vendor_update: bool) -> int:
    from basalt import catalogue, stack
    from basalt.review import tool_states

    async def run():
        fleet = stack.build_fleet()
        if vendor_update:
            fleet.merchants.ship_update()
        async with stack.servers_only(fleet):
            return await tool_states(catalogue.load())

    states = asyncio.run(run())
    print("Each approved server's live tools against their pins" + (" after Ledgerline's 2.1 update:" if vendor_update else ":"))
    for (server, tool), state in states.items():
        print(f"  {server + '.' + tool:<28}{state}")
    drifted = [key for key, state in states.items() if state != "pinned"]
    print(f"\n{len(drifted)} tools differ from what the reviewer approved." if drifted else "\nEvery tool matches its pin.")
    return 0


def fail_closed() -> int:
    from basalt.stack import fail_closed as run_checks

    checks = asyncio.run(run_checks())
    for row in checks:
        outcome = "ran      " if row["ran"] else "refused  "
        print(f"  {row['case']:<28}{outcome}{row['refused'][:90]}")
    broken = [row for row in checks[1:] if row["ran"]]
    print(f"\n{len(checks) - 1 - len(broken)} of {len(checks) - 1} failures refused the call.")
    return 1 if broken or checks[0]["ran"] is False else 0


def show(step: dict) -> None:
    if step["kind"] == "call":
        arguments = ", ".join(f"{name}={value!r}" for name, value in step["arguments"].items())
        print(f"  {'REFUSED ' if step['error'] else 'call    '}{step['tool']}({arguments[:80]})")
        print(f"          {step['result'].replace(chr(10), ' ')[:110]}")


def one_request(request_id: str, direct: bool) -> int:
    from basalt.review import request, run_mode

    chosen, mode = request(request_id), "direct" if direct else "gateway"
    client, model_name = model()
    print(f"{chosen['id']} as {chosen['person']}, {mode}: {chosen['ask']}\n")
    result = asyncio.run(run_mode(mode, client, model_name, [chosen], lambda row: None))
    row = result["rows"][0]
    for call in row["tried"]:
        show({"kind": "call", **call})
    print(f"\nAgent: {row['reply'].strip()}\n")
    print("Servers ran: " + (", ".join(f"{call['server']}.{call['tool']}" for call in row["executed"]) or "nothing"))
    for call in row["got_through"]:
        print(f"GOT THROUGH: {call['server']}.{call['tool']} ({call['why']})")
    print(f"Calls that got through: {len(row['got_through'])}. Token seen upstream: {row['tokens_seen_upstream']} times. "
          f"${row['cost']:.4f}.")
    return 0


def review_all() -> int:
    from basalt.review import REVIEW, review

    client, model_name = model()
    print(f"10 staff requests, direct and through the gateway, after the vendor's update. Model {model_name}.\n")
    results = asyncio.run(review(client, model_name, lambda row: print(
        f"  {row['mode']:<8}{row['id']}  {row['team']:<20}ran {len(row['executed'])}  "
        f"got through {len(row['got_through'])}")))
    print(f"\n{'':<10}{'got through':<13}{'outside team':<14}{'token upstream':<16}{'unlogged':<10}cost")
    for mode in ("direct", "gateway"):
        totals = results[mode]["score"]
        unlogged = "-" if totals["unlogged"] is None else totals["unlogged"]
        print(f"{mode:<10}{totals['got_through']:<13}{totals['outside_team']:<14}{totals['tokens_seen_upstream']:<16}"
              f"{unlogged!s:<10}${totals['cost']:.4f}")
    print(f"\nEvery request, call and refusal: {REVIEW}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gateway", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("catalogue", help="the approved servers, owners, team tool lists and pins")
    check_parser = commands.add_parser("check", help="compare each approved server's live tools to their pins")
    check_parser.add_argument("--vendor-update", action="store_true", help="ship Ledgerline's 2.1 update first")
    commands.add_parser("fail-closed", help="break the list and the pin check, and show calls are refused")
    ask_parser = commands.add_parser("ask", help="run one staff request, like BP-02")
    ask_parser.add_argument("request_id")
    ask_parser.add_argument("--direct", action="store_true", help="the naive config: every server URL, no gateway")
    commands.add_parser("review", help="all ten requests, direct and through the gateway")
    serve_parser = commands.add_parser("serve", help="the platform team's approval console")
    serve_parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    quiet()
    if args.command == "catalogue":
        return show_catalogue()
    if args.command == "check":
        return check(args.vendor_update)
    if args.command == "fail-closed":
        return fail_closed()
    if args.command == "ask":
        return one_request(args.request_id, args.direct)
    if args.command == "review":
        return review_all()
    from basalt.serve import main as serve

    return serve(args.port)


if __name__ == "__main__":
    sys.exit(main())
