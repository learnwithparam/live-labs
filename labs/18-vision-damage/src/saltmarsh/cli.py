"""`claims`: price a claim from confirmed findings, read one photo, assess the queue, run the naive
baseline, or open the estimator page."""

import argparse
import asyncio
import json
import sys


def money(amount: float | None) -> str:
    return "none" if amount is None else f"£{amount:,.2f}"


def print_estimate(estimate: dict) -> None:
    for item in estimate["lines"]:
        note = "  (lamps, glass and wheels are always replaced)" if item["overridden"] else ""
        print(f"  {item['panel']:<20}{item['action']:<9}{item['severity']:<10}parts {money(item['parts']):>10}"
              f"  labour {money(item['labour']):>9}  paint {money(item['paint']):>8}  {money(item['total']):>10}{note}")
    print(f"  {'estimate':<39}{money(estimate['total']):>66}")


def price_confirmed(claim_id: str) -> int:
    """The adjuster's own findings through the pricing code: no model, no key."""
    from saltmarsh.claims import claim, rates, truth
    from saltmarsh.pricing import merge, price
    from saltmarsh.triage import covered

    found, adjuster = claim(claim_id), truth()
    readings = [{"photo": photo_id, **adjuster["photos"][photo_id]} for photo_id in found["photos"]]
    estimate = price(merge(covered(readings)), found["vehicle"], rates())
    vehicle = found["vehicle"]
    print(f"{found['id']}  {vehicle['year']} {vehicle['make']} {vehicle['model']}, value {money(vehicle['value'])}\n")
    print_estimate(estimate)
    bill = adjuster["claims"][found["id"]].get("bill")
    print(f"\nRepairer's bill: {money(bill)}" if bill else "\nNo bill: an adjuster settled this claim.")
    return 0


def read(photo_id: str) -> int:
    from saltmarsh.claims import claims
    from saltmarsh.config import model_client
    from saltmarsh.reader import read_photo

    owner = next((each for each in claims() if photo_id in each["photos"]), None)
    if not owner:
        print(f"No photo {photo_id}. Photos run from P01 to P12.")
        return 1
    client, model_name = model_client()
    reading = asyncio.run(read_photo(client, model_name, owner, photo_id))
    print(json.dumps({key: value for key, value in reading.items() if key != "usage"}, indent=1))
    usage = reading["usage"]
    print(f"\n{usage['prompt_tokens']:,} tokens in, {usage['completion_tokens']:,} out, ${usage['cost']:.5f}.")
    return 0


def summary(run: dict) -> str:
    score, panels = run["score"], run["score"]["panels"]
    return (f"{score['within_tolerance']} of {score['billed']} estimates within 20% of the bill. "
            f"{score['auto']} auto-approved, {score['adjuster']} sent to an adjuster; "
            f"{score['routed_right']} of {score['claims']} routed right, "
            f"{score['reasons_caught']} of {score['expected_adjuster']} with the adjuster's reason.\n"
            f"Panels: {panels['right']} of {panels['confirmed']} confirmed panels found, "
            f"{panels['right']} of {panels['found']} named panels right. "
            f"{run['usage']['prompt_tokens']:,} tokens, ${run['usage']['cost']:.4f} for the queue.")


def assess_all() -> int:
    from saltmarsh.assess import assess
    from saltmarsh.claims import truth
    from saltmarsh.config import model_client

    client, model_name = model_client()
    print(f"Reading every claim photo with {model_name}.\n")
    run, adjuster = asyncio.run(assess(client, model_name)), truth()
    for result in run["results"]:
        bill = adjuster["claims"][result["claim"]].get("bill")
        reasons = ", ".join(reason["code"] for reason in result["route"]["reasons"])
        print(f"  {result['claim']}  {result['route']['to']:<9}estimate {money(result['estimate']['total']):>10}"
              f"  bill {money(bill):>10}  {reasons}")
    print("\n" + summary(run))
    print("Every reading, estimate and route: out/run.json")
    return 0


def naive_all() -> int:
    from saltmarsh.assess import naive
    from saltmarsh.config import model_client

    client, model_name = model_client()
    print(f"Asking {model_name} for a free-text estimate on each repaired claim.\n")
    run = asyncio.run(naive(client, model_name))
    for estimate in run["estimates"]:
        print(f"  {estimate['claim']}  said {money(estimate['amount']):>10}  bill {money(estimate['bill']):>10}"
              f"  {'within' if estimate['within'] else 'outside'} 20%")
    print(f"\nFree text: {run['within_tolerance']} of {run['billed']} estimates within 20% of the bill, "
          f"${run['usage']['cost']:.4f}. Answers in out/naive.json")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="claims")
    sub = parser.add_subparsers(dest="command", required=True)
    pricing = sub.add_parser("price")
    pricing.add_argument("claim")
    pricing.add_argument("--confirmed", action="store_true", required=True,
                         help="price the adjuster's confirmed findings")
    sub.add_parser("read").add_argument("photo")
    sub.add_parser("assess")
    sub.add_parser("naive")
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "price":
        sys.exit(price_confirmed(args.claim))
    if args.command == "read":
        sys.exit(read(args.photo))
    if args.command == "assess":
        sys.exit(assess_all())
    if args.command == "naive":
        sys.exit(naive_all())
    from saltmarsh.serve import main as serve

    sys.exit(serve(args.port))
