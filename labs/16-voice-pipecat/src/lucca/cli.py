import argparse

from lucca.till import Menu


def show_menu() -> None:
    menu = Menu()
    print(f"{menu.data['restaurant']}, {menu.data['address']}")
    for item in menu.data["items"]:
        prices = item.get("sizes") or {"each": item.get("price")}
        print(f"  {item['name']}: " + ", ".join(f"{size} ${price:.2f}" for size, price in prices.items()))


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
    review_parser = commands.add_parser("review", help="play the recorded calls into the line and score it")
    review_parser.add_argument("call_ids", nargs="*")
    serve_parser = commands.add_parser("serve", help="open the kitchen ticket display")
    serve_parser.add_argument("--port", type=int, default=8016)
    arguments = parser.parse_args()
    if arguments.command == "menu":
        show_menu()
    elif arguments.command == "review":
        show_review(arguments.call_ids)
    else:
        from lucca.serve import serve

        serve(arguments.port)
