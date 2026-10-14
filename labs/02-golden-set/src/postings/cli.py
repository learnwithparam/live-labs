"""`postings try PROMPT FILE...`, `eval PROMPT`, `gate BEFORE AFTER` and `serve`."""

import argparse
import sys
from pathlib import Path

from postings.schema import FIELDS

GOLDEN = Path("data/golden.jsonl")
POSTINGS = Path("data/postings")
RUNS = Path("runs")


def client_and_model():
    from openai import OpenAI

    from postings.config import settings

    base_url, key, model = settings()
    return OpenAI(base_url=base_url, api_key=key, timeout=60, max_retries=4), model


def shown(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.0f}" if value.is_integer() else f"{value:g}"
    return "-" if value is None else str(value)


def try_prompt(prompt: Path, paths: list[Path]) -> int:
    """What everyone does first: run the change on a few postings and read the output."""
    from postings.extract import read_all

    client, model = client_and_model()
    for name, reading in read_all({p.name: p.read_text() for p in paths}, prompt.read_text(), client, model).items():
        print(f"{name:28} " + "  ".join(f"{f}={shown(reading.fields[f])}" for f in FIELDS))
    print(f"{len(paths)} postings read with {prompt.name} on {model}")
    return 0


def evaluate(prompt: Path, golden_path: Path, runs: Path) -> int:
    from postings.extract import read_all
    from postings.score import accuracy, load_golden, save_run

    client, model = client_and_model()
    golden = load_golden(golden_path)
    readings = read_all({name: (POSTINGS / name).read_text() for name in golden}, prompt.read_text(), client, model)
    fields = {name: r.fields for name, r in readings.items()}
    cost = sum(r.cost for r in readings.values())
    save_run(runs / f"{prompt.stem}.json", prompt.stem, model, fields, cost)
    scores = accuracy(golden, fields)
    for f in FIELDS:
        print(f"{f:12} {scores[f]:2}/{len(golden)}")
    print(f"{prompt.stem}: {sum(scores.values())}/{len(golden) * len(FIELDS)} fields right, cost ${cost:.4f} on {model}, saved {runs / prompt.stem}.json")
    return 0


def gate_runs(before_path: Path, after_path: Path, golden_path: Path) -> int:
    from postings.score import gate, load_golden, load_run

    golden = load_golden(golden_path)
    before, after = load_run(before_path), load_run(after_path)
    verdict = gate(golden, before["readings"], after["readings"])
    for f in FIELDS:
        mark = "  worse" if f in verdict.worse else ""
        print(f"{f:12} {verdict.before[f]:2}/{verdict.total} -> {verdict.after[f]:2}/{verdict.total}{mark}")
    for c in verdict.regressed:
        print(f"  broke {c.posting}: {c.field} approved {shown(c.approved)}, was {shown(c.before)}, now {shown(c.after)}")
    for c in verdict.fixed:
        print(f"  fixed {c.posting}: {c.field} approved {shown(c.approved)}, was {shown(c.before)}, now {shown(c.after)}")
    held = verdict.total - len(verdict.needs_person)
    print(f"{held} of {verdict.total} postings held, {len(verdict.needs_person)} need a person")
    if verdict.blocked:
        print(f"blocked: {after['prompt']} makes {', '.join(verdict.worse)} worse than {before['prompt']}")
        return 1
    print(f"passed: no field is worse with {after['prompt']}")
    return 0


def serve_page(port: int) -> int:
    from postings.serve import Bench, serve

    client, model = client_and_model()
    server = serve(Bench(Path("prompts"), POSTINGS, GOLDEN, client, model), port)
    print(f"Golden set bench on http://127.0.0.1:{port} with {model}. Ctrl-C stops it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="postings")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("try")
    one.add_argument("prompt", type=Path)
    one.add_argument("paths", type=Path, nargs="+")
    run = sub.add_parser("eval")
    run.add_argument("prompt", type=Path)
    run.add_argument("--golden", type=Path, default=GOLDEN)
    run.add_argument("--runs", type=Path, default=RUNS)
    check = sub.add_parser("gate")
    check.add_argument("before", type=Path)
    check.add_argument("after", type=Path)
    check.add_argument("--golden", type=Path, default=GOLDEN)
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "try":
        sys.exit(try_prompt(args.prompt, args.paths))
    if args.command == "eval":
        sys.exit(evaluate(args.prompt, args.golden, args.runs))
    if args.command == "gate":
        sys.exit(gate_runs(args.before, args.after, args.golden))
    sys.exit(serve_page(args.port))
