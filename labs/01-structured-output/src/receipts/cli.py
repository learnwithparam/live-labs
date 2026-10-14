"""`receipts naive DIR`, `check FILE.json`, `extract FILE`, `match DIR` and `serve DIR`."""

import argparse
import json
import re
import sys
from pathlib import Path

from pydantic import ValidationError

from receipts.extract import IMAGE_TYPES, ExtractionFailed, extract, naive, read_receipt
from receipts.schema import Receipt


def client_and_model():
    from openai import OpenAI

    from receipts.config import settings

    base_url, key, model = settings()
    return OpenAI(base_url=base_url, api_key=key, timeout=60, max_retries=4), model


def receipts_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in {".txt", ".pdf", *IMAGE_TYPES})


def totals_in(reply: dict) -> dict:
    """Every top-level key that could be the total; a naive reply names its own, sometimes two."""
    return {k: v for k, v in reply.items() if re.search(r"total|balance|amount_due", k, re.I) and not re.search(r"sub", k, re.I)}


def naive_all(folder: Path) -> int:
    client, model = client_and_model()
    paths = receipts_in(folder)
    fenced, shapes, unusable = 0, set(), 0
    for path in paths:
        reply = naive(read_receipt(path), client, model)
        try:
            json.loads(reply)
            note = "plain JSON"
        except json.JSONDecodeError:
            fenced += 1
            note = "not JSON as sent"
        # Strip a Markdown fence by hand, the way people do, to see what is inside.
        inner = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", reply.strip())
        try:
            parsed = json.loads(inner)
        except json.JSONDecodeError:
            print(f"{path.name:24} {note}, and not JSON inside either")
            continue
        shapes.add(tuple(sorted(parsed)))
        totals = totals_in(parsed)
        # A ledger needs one number under one known key; anything else means code has to guess.
        unusable += not isinstance(parsed.get("total"), (int, float))
        shown = ", ".join(f"{k}={v!r}" for k, v in totals.items()) or "no total key"
        print(f"{path.name:24} {note:16} {len(parsed):2} keys  {shown}")
    print(f"{fenced}/{len(paths)} not JSON as sent, {len(shapes)} different shapes, {unusable} with no number under `total`, on {model}")
    return 0


def check(path: Path) -> int:
    try:
        receipt = Receipt.model_validate_json(path.read_text())
    except ValidationError as error:
        print(f"rejected: {error.errors()[0]['msg']}")
        return 1
    print(f"valid: {receipt.merchant} {receipt.currency} {receipt.total:.2f}")
    return 0


def extract_one(path: Path, retries: bool) -> int:
    client, model = client_and_model()
    try:
        result = extract(read_receipt(path), client, model, max_attempts=2 if retries else 1)
    except ExtractionFailed as error:
        print(error)
        return 1
    print(result.receipt.model_dump_json(indent=2))
    print(f"valid and reconciled on attempt {result.attempts} with {model}")
    return 0


FEED = Path("data/card-transactions.csv")


def match_all(folder: Path, feed: Path) -> int:
    from receipts.match import load_charges, settle

    client, model = client_and_model()
    charges, taken = load_charges(feed), set()
    paths = receipts_in(folder)
    person, cost = [], 0.0
    for path in paths:
        settled = settle(read_receipt(path), client, model, charges, taken)
        cost += settled.cost
        charge = settled.match.charge
        if charge is None:
            person.append(path.name)
        shown = f"{charge.id} {charge.descriptor}" if charge else "needs a person"
        print(f"{path.name:22} -> {shown:36} {settled.match.reason}")
    matched = len(paths) - len(person)
    print(f"{matched}/{len(paths)} matched to a card charge, {len(person)} need a person, cost ${cost:.4f} on {model}")
    return 0


def serve_desk(folder: Path, feed: Path, port: int) -> int:
    from receipts.match import load_charges
    from receipts.serve import Desk, serve

    client, model = client_and_model()
    server = serve(Desk(folder, load_charges(feed), client, model), port)
    print(f"Receipt desk on http://127.0.0.1:{port} with {model}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="receipts")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("naive").add_argument("path", type=Path)
    sub.add_parser("check").add_argument("path", type=Path)
    one = sub.add_parser("extract")
    one.add_argument("path", type=Path)
    one.add_argument("--no-retry", action="store_true")
    every = sub.add_parser("match")
    every.add_argument("path", type=Path)
    every.add_argument("--feed", type=Path, default=FEED)
    desk = sub.add_parser("serve")
    desk.add_argument("path", type=Path)
    desk.add_argument("--feed", type=Path, default=FEED)
    desk.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "naive":
        sys.exit(naive_all(args.path))
    if args.command == "check":
        sys.exit(check(args.path))
    if args.command == "extract":
        sys.exit(extract_one(args.path, retries=not args.no_retry))
    if args.command == "serve":
        sys.exit(serve_desk(args.path, args.feed, args.port))
    sys.exit(match_all(args.path, args.feed))
