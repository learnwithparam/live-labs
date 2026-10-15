import argparse
import asyncio
import json

from lucca.caller import load_calls
from lucca.till import Menu


def show_menu() -> None:
    menu = Menu()
    print(f"{menu.data['restaurant']}, {menu.data['address']}")
    for item in menu.data["items"]:
        prices = item.get("sizes") or {"each": item.get("price")}
        print(f"  {item['name']}: " + ", ".join(f"{size} ${price:.2f}" for size, price in prices.items()))


def show_call(call_id: str) -> None:
    from lucca.pipeline import run_call

    call = next(call for call in load_calls() if call["id"] == call_id)
    result = asyncio.run(run_call(call, 400))
    for turn in result["turns"]:
        print(f"caller: {turn['heard']!r}  latency {turn['latency_ms']} ms "
              f"(hear {turn['hear_ms']}, think {turn['think_ms']}, speak {turn['speak_ms']})")
    print(json.dumps(result["ticket"], indent=2))
    print(f"cost ${result['cost']:.4f}")


def show_review(call_ids: list[str]) -> None:
    from lucca.review import review

    metric = review(call_ids or None)["metric"]
    print(f"taken with no staff: {metric['taken_alone']} of {metric['orders']} orders")
    print(f"sent to staff correctly: {metric['staff_correct']} of {metric['staff_expected']}")
    print(f"turn latency: p50 {metric['p50_ms']} ms, p95 {metric['p95_ms']} ms over {metric['timed_turns']} turns")
    print(f"cost ${metric['cost']:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="phoneline", description="Lucca Brick Oven's phone ordering line")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("menu", help="print the menu the till accepts")
    commands.add_parser("voices", help="record the scripted callers with local text to speech")
    call_parser = commands.add_parser("call", help="play one recorded call through the pipeline")
    call_parser.add_argument("call_id")
    review_parser = commands.add_parser("review", help="play every recorded call and score the line")
    review_parser.add_argument("call_ids", nargs="*")
    serve_parser = commands.add_parser("serve", help="open the kitchen ticket display")
    serve_parser.add_argument("--port", type=int, default=8016)
    arguments = parser.parse_args()
    if arguments.command == "menu":
        show_menu()
    elif arguments.command == "voices":
        from lucca.voices import record_calls

        print(f"recorded {len(record_calls())} new lines")
    elif arguments.command == "call":
        show_call(arguments.call_id)
    elif arguments.command == "review":
        show_review(arguments.call_ids)
    else:
        from lucca.serve import serve

        serve(arguments.port)
