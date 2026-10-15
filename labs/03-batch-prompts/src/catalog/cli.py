"""`catalog one CSV`, `file CSV`, `run FILE`, `submit FILE`, `collect ID` and `serve`."""

import argparse
import json
import sys
from pathlib import Path

CATALOGUE = Path("data/catalogue.csv")
SUPPLIED = Path("data/retailer-attributes.csv")
OUT = Path("out")
CHECKPOINT = OUT / "done.jsonl"


def client_and_model():
    from openai import OpenAI

    from catalog.config import settings

    base_url, key, model = settings()
    return OpenAI(base_url=base_url, api_key=key, timeout=120, max_retries=4), model


def report(label: str, requests: int, tally) -> None:
    print(f"{label}: {requests} requests for {tally.products} products, cost ${tally.cost:.4f}, ${tally.per_thousand:.4f} per 1,000 products")
    print(f"{tally.right}/{tally.total} attributes match the retailer, {tally.filled} of {tally.products} filled with no person, {len(tally.person)} need a person")


def one_at_a_time(catalogue: Path) -> int:
    """The obvious way: one request per product, the instructions sent again every time."""
    from catalog.fill import one_lines, run_lines, settle
    from catalog.score import load_products, load_supplied, tally

    client, model = client_and_model()
    products = load_products(catalogue)
    checkpoint = OUT / "one.jsonl"
    checkpoint.unlink(missing_ok=True)
    lines = one_lines(products, model)
    run_lines(lines, client, checkpoint)
    filled, cost = settle(checkpoint, {product.sku: product for product in products}, client, model)
    report(f"one at a time on {model}", len(lines), tally(products, filled, load_supplied(SUPPLIED), cost))
    return 0


def write_file(catalogue: Path, size: int, out: Path) -> int:
    from catalog.config import settings  # the model name goes in every request body
    from catalog.fill import batch_lines
    from catalog.score import load_products

    _, _, model = settings()
    products = load_products(catalogue)
    lines = batch_lines(products, model, size)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(line) + "\n" for line in lines))
    print(f"{len(lines)} requests for {len(products)} products, {size} per request, written to {out}")
    return 0


def read_file(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_file(path: Path, catalogue: Path) -> int:
    from catalog.fill import run_lines, settle
    from catalog.score import load_products, load_supplied, tally

    client, model = client_and_model()
    lines = read_file(path)
    sent = run_lines(lines, client, CHECKPOINT)
    print(f"sent {sent['sent']} requests, {sent['skipped']} already done in {CHECKPOINT}")
    products = load_products(catalogue)
    filled, cost = settle(CHECKPOINT, {product.sku: product for product in products}, client, model)
    report(f"packed on {model}", len(lines), tally(products, filled, load_supplied(SUPPLIED), cost))
    return 0


BATCH_CHECKPOINT = OUT / "batch-done.jsonl"
BATCH_ID = OUT / "batch-id.txt"


def batch_client():
    from anthropic import Anthropic

    from catalog.config import batch_settings

    key, model = batch_settings()
    return Anthropic(api_key=key), model


def submit_file(path: Path, limit: int) -> int:
    from catalog.fill import submit

    client, model = batch_client()
    batch_id = submit(read_file(path), client, model, limit)
    BATCH_ID.write_text(batch_id)
    # A new batch replaces the last one's results, so collect never scores rows from an older batch.
    BATCH_CHECKPOINT.unlink(missing_ok=True)
    print(f"submitted {min(limit, len(read_file(path)))} requests to {model} as batch {batch_id}")
    print("`catalog collect --wait` once it ends, within 24 hours")
    return 0


def collect_batch(batch_id: str | None, path: Path, catalogue: Path, wait: bool) -> int:
    import time

    from catalog.fill import collect, filled_in, read_jsonl, skus_in
    from catalog.score import load_products, load_supplied, tally

    client, model = batch_client()
    if not batch_id and not BATCH_ID.exists():
        print("no batch submitted yet: run `catalog submit FILE` first")
        return 1
    batch_id = batch_id or BATCH_ID.read_text().strip()
    result = collect(batch_id, client)
    if result is None and wait:
        print(f"waiting for batch {batch_id} to end")
    while result is None and wait:
        time.sleep(10)
        result = collect(batch_id, client)
    if result is None:
        print(f"batch {batch_id} is still running")
        return 1
    rows, errors = result
    for error in errors:
        print(f"failed {error}")
    if not rows:
        return 1
    lines = {line["custom_id"]: line for line in read_file(path)}
    done = {row["custom_id"] for row in read_jsonl(BATCH_CHECKPOINT)}
    BATCH_CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    with BATCH_CHECKPOINT.open("a") as out:
        for row in rows:
            if row["custom_id"] not in done:
                out.write(json.dumps({**row, "skus": skus_in(lines[row["custom_id"]]["body"])}) + "\n")
    filled, cost, _ = filled_in(BATCH_CHECKPOINT)
    asked = {sku for row in read_jsonl(BATCH_CHECKPOINT) for sku in row["skus"]}
    products = [product for product in load_products(catalogue) if product.sku in asked]
    supplied = {sku: row for sku, row in load_supplied(SUPPLIED).items() if sku in asked}
    report(f"batch on {model}", len(rows), tally(products, filled, supplied, cost))
    return 0


def serve_page(port: int) -> int:
    from catalog.serve import Shelf, serve

    client, model = client_and_model()
    server = serve(Shelf(CATALOGUE, SUPPLIED, client, model), port)
    print(f"Catalogue filler on http://127.0.0.1:{port} with {model}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="catalog")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("one").add_argument("catalogue", type=Path)
    make = sub.add_parser("file")
    make.add_argument("catalogue", type=Path)
    make.add_argument("--size", type=int, default=24)
    make.add_argument("-o", "--out", type=Path, default=OUT / "batch.jsonl")
    run = sub.add_parser("run")
    run.add_argument("path", type=Path)
    run.add_argument("--catalogue", type=Path, default=CATALOGUE)
    send = sub.add_parser("submit")
    send.add_argument("path", type=Path)
    send.add_argument("--limit", type=int, default=5, help="requests to send; 5 keeps a demo under a cent")
    take = sub.add_parser("collect")
    take.add_argument("batch_id", nargs="?", help="defaults to the last batch submitted")
    take.add_argument("--file", type=Path, default=OUT / "batch.jsonl")
    take.add_argument("--catalogue", type=Path, default=CATALOGUE)
    take.add_argument("--wait", action="store_true", help="poll every 10 seconds until the batch ends")
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "one":
        sys.exit(one_at_a_time(args.catalogue))
    if args.command == "file":
        sys.exit(write_file(args.catalogue, args.size, args.out))
    if args.command == "run":
        sys.exit(run_file(args.path, args.catalogue))
    if args.command == "submit":
        sys.exit(submit_file(args.path, args.limit))
    if args.command == "collect":
        sys.exit(collect_batch(args.batch_id, args.file, args.catalogue, args.wait))
    sys.exit(serve_page(args.port))
