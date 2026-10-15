"""`store`: list what the model sees, run one task, review all 20, run the server, or open the admin."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

REVIEW = Path("out/review.json")
TIMEOUT = 60  # seconds per model call


def model():
    from openai import AsyncOpenAI

    from tidepool.config import llm_settings

    base_url, api_key, model_name = llm_settings()
    return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2), model_name


def show(step: dict) -> None:
    if step["kind"] == "call":
        args = ", ".join(f"{name}={value!r}" for name, value in step["args"].items())
        print(f"  {'ERROR ' if step['error'] else 'call  '}{step['tool']}({args[:90]})")
        print(f"        {step['result'].replace(chr(10), ' ')[:110]}")


def list_tools(server: str) -> int:
    from mcp.client import Client

    from tidepool.review import NAMES, SERVERS
    from tidepool.store import Store

    async def run():
        async with Client(SERVERS[server](Store())) as mcp:
            return (await mcp.list_tools()).tools

    tools = asyncio.run(run())
    size = sum(len(json.dumps(tool.model_dump(by_alias=True, exclude_none=True))) for tool in tools)
    print(f"{NAMES[server]}: {len(tools)} tools, about {size // 4} tokens sent with every model call.\n")
    for tool in tools:
        hint = tool.annotations and ("read-only" if tool.annotations.read_only_hint else
                                     "destructive" if tool.annotations.destructive_hint else "")
        summary = (tool.description or "").splitlines()[0][:80]
        print(f"  {tool.name:<25}{summary}" + (f"  [{hint}]" if hint else ""))
    return 0


def one_task(task_id: str, server: str) -> int:
    from tidepool.review import NAMES, run_task, task

    chosen = task(task_id)
    client, model_name = model()
    print(f"{chosen['id']} on {NAMES[server]}. Merchant: {chosen['ask']}\n")
    row = asyncio.run(run_task(server, chosen, client, model_name, show))
    print(f"\nAssistant: {row['reply'].strip()}\n")
    print("Store: " + ("; ".join(f"{path} = {value}" for path, value in row["changes"].items()) or "no change"))
    wrong = sum(call["error"] for call in row["calls"])
    print(f"Result: {'success' if row['ok'] else 'failed'} ({row['why']}). "
          f"{len(row['calls'])} tool calls, {wrong} wrong, ${row['cost']:.4f}.")
    return 0


def review_all() -> int:
    from tidepool.review import NAMES, review

    client, model_name = model()
    print(f"20 merchant tasks on each MCP server, each on a fresh store. Model {model_name}.\n")
    results = asyncio.run(review(client, model_name, lambda row: print(
        f"  {NAMES[row['server']]:<15}{row['task']}  {'ok    ' if row['ok'] else 'FAILED'}  {row['why']}")))
    print(f"\n{'':<16}{'succeeded':<12}{'wrong calls':<13}{'tool calls':<12}{'tools':<7}{'prompt tokens':<15}cost")
    for server, result in results.items():
        totals = result["score"]
        print(f"{NAMES[server]:<16}{totals['succeeded']} of {totals['tasks']:<6}{totals['wrong_calls']:<13}"
              f"{totals['calls']:<12}{totals['tools']:<7}{totals['prompt_tokens']:<15}${totals['cost']:.4f}")
    REVIEW.parent.mkdir(exist_ok=True)
    REVIEW.write_text(json.dumps(results, indent=1))
    print(f"\nEvery task, call and change: {REVIEW}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="store")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tools").add_argument("--server", choices=["api", "designed"], default="designed")
    one = sub.add_parser("ask")
    one.add_argument("task", help="a task id, TP-01 to TP-20")
    one.add_argument("--server", choices=["api", "designed"], default="designed")
    sub.add_parser("review")
    sub.add_parser("mcp", help="run the designed server over stdio, for Claude Code or any MCP client")
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "tools":
        sys.exit(list_tools(args.server))
    if args.command == "ask":
        sys.exit(one_task(args.task, args.server))
    if args.command == "review":
        sys.exit(review_all())
    if args.command == "mcp":
        from tidepool.store import Store
        from tidepool.tools_server import build

        build(Store()).run("stdio")
        return
    from tidepool.serve import main as serve

    sys.exit(serve(args.port))
