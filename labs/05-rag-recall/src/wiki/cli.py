"""`wiki ingest`, `wiki search`, `wiki eval` and `wiki serve`."""

import argparse
import os
import sys
import warnings
from pathlib import Path

DOCS = Path("data/docs")
GOLDEN = Path("data/golden.jsonl")
# fastembed draws a download bar per file; the lab's output stays readable without them.
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
warnings.filterwarnings("ignore", message="Cannot enable progress bars")


def build():
    from wiki.encoders import FastEmbed
    from wiki.ingest import ingest
    from wiki.search import Search

    return Search(ingest(DOCS), FastEmbed())


def show_ingest(docs: Path) -> int:
    from wiki.ingest import ingest

    passages = ingest(docs)
    print(f"{len(passages)} sections from {len(list(docs.glob('*.pdf')))} PDFs")
    for passage in passages[:3]:
        print(f"  [{passage.id}] {passage.doc}, page {passage.page}")
    return 0


def search(question: str, how: str) -> int:
    from wiki.score import load_golden

    label = next((label.passage for label in load_golden(GOLDEN) if label.question == question), None)
    for rank, hit in enumerate(build().find(question, how), 1):
        mark = "  <- labelled" if hit.passage.id == label else ""
        print(f"{rank}. {hit.passage.id}  {hit.score:.3f}{mark}")
    return 0


def evaluate(ways: list[str]) -> int:
    from wiki.score import KS, load_golden
    from wiki.score import evaluate as run

    golden = load_golden(GOLDEN)
    result = run(build(), golden, tuple(ways))
    print(f"{result['total']} labelled questions\n")
    print(f"{'':11}" + "".join(f"{'recall@' + str(top_k):<12}" for top_k in KS))
    for how in ways:
        print(f"{how:11}" + "".join(f"{str(result['recall'][how][top_k]) + ' of ' + str(result['total']):<12}" for top_k in KS))
    print(f"\nNeeds a person ({len(result['person'])}): the labelled section is not in the top 5 on {ways[-1]}")
    for question in result["person"]:
        print(f"  {question}")
    return 0


def serve_page(port: int) -> int:
    from wiki.score import load_golden
    from wiki.serve import Room, serve

    server = serve(Room(build(), load_golden(GOLDEN), Path("out/review.jsonl")), port)
    print(f"Runbook search on http://127.0.0.1:{port}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main() -> None:
    from wiki.search import WAYS

    parser = argparse.ArgumentParser(prog="wiki")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest").add_argument("docs", type=Path)
    find = sub.add_parser("search")
    find.add_argument("question")
    find.add_argument("--how", choices=WAYS, default="rerank")
    sub.add_parser("eval").add_argument("--how", choices=WAYS, action="append")
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "ingest":
        sys.exit(show_ingest(args.docs))
    if args.command == "search":
        sys.exit(search(args.question, args.how))
    if args.command == "eval":
        sys.exit(evaluate(args.how or list(WAYS)))
    sys.exit(serve_page(args.port))
