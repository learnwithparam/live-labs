"""`oncall ingest`, `oncall blind`, `oncall ask`, `oncall eval` and `oncall serve`."""

import argparse
import sys
from pathlib import Path

DOCS = Path("data/docs")
QUESTIONS = Path("data/questions.jsonl")


def client_and_model():
    from openai import OpenAI

    from oncall.config import settings

    base_url, key, model = settings()
    return OpenAI(base_url=base_url, api_key=key, timeout=120, max_retries=4), model


def show_ingest(docs: Path, how: str) -> int:
    from oncall.ingest import ingest

    passages = ingest(docs, how)
    print(f"{len(passages)} passages by {how} from {len(list(docs.glob('*.pdf')))} PDFs")
    for passage in passages[:3]:
        print(f"\n[{passage.id}] page {passage.page}\n{passage.text}")
    return 0


def blind(question: str) -> int:
    from oncall.answer import ask_blind

    client, model = client_and_model()
    print(f"{model}, no docs:\n{ask_blind(question, client, model)}")
    return 0


def ask(question: str, how: str) -> int:
    from oncall.desk import Desk

    client, model = client_and_model()
    reply = Desk(DOCS, client, model, how).ask(question)
    print(f"found: {', '.join(passage.id for passage in reply.passages)}")
    print(f"answer: {reply.answer}\ncited: {', '.join(reply.cited) or 'nothing'}")
    return 0


def evaluate(how: str) -> int:
    from oncall.desk import Desk
    from oncall.score import load_questions

    client, model = client_and_model()
    result = Desk(DOCS, client, model, how).run(load_questions(QUESTIONS))
    for row in result["rows"]:
        mark = "ok    " if row["supported"] else "person"
        print(f"{mark} {row['question'][:70]}" + (f"\n       {row['reason']}" if row["reason"] else ""))
    tally = result["tally"]
    print(f"\n{how} passages on {model}: {tally['supported']} of {tally['answered']} answers supported by their citation ({tally['share']:.0%}), "
          f"{tally['person']} of {tally['questions']} questions go to a person, cost ${tally['cost']:.4f}")
    return 0


def serve_page(port: int) -> int:
    from oncall.serve import Room, serve

    client, model = client_and_model()
    server = serve(Room(DOCS, QUESTIONS, client, model), port)
    print(f"On-call desk on http://127.0.0.1:{port} with {model}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="oncall")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("ingest", "ask", "eval"):
        command = sub.add_parser(name)
        command.add_argument("--by", choices=["section", "size"], default="section")
        if name == "ingest":
            command.add_argument("docs", type=Path)
        if name == "ask":
            command.add_argument("question")
    sub.add_parser("blind").add_argument("question")
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "ingest":
        sys.exit(show_ingest(args.docs, args.by))
    if args.command == "blind":
        sys.exit(blind(args.question))
    if args.command == "ask":
        sys.exit(ask(args.question, args.by))
    if args.command == "eval":
        sys.exit(evaluate(args.by))
    sys.exit(serve_page(args.port))
