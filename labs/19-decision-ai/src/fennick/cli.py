"""`refunds`: write the history, train the model, back-test it, open one decision, ask an LLM for scores,
or open the review queue."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

OUT = Path("out")
BACKTEST, DECISIONS, LLM = OUT / "backtest.json", OUT / "decisions.jsonl", OUT / "llm.json"


def history() -> int:
    from fennick.history import HISTORY, generate, is_fraud, write

    rows = generate()
    write(rows)
    frauds = sum(map(is_fraud, rows))
    print(f"{len(rows):,} refunds from {rows[0]['requested_at']} to {rows[-1]['requested_at']}, "
          f"{frauds} found to be fraud later ({frauds / len(rows):.1%}). Written to {HISTORY}.")
    return 0


def train() -> int:
    from fennick import history, model

    models = model.train(history.load())
    version = model.save(models)
    print(f"Trained on {models['trained_on']:,} refunds (January to April), "
          f"calibrated on {models['calibrated_on']:,} (May).")
    print(f"Model version {version}, saved to {model.MODEL_FILE}.")
    return 0


def percent(share: float) -> str:
    return f"{share:.1%}"


def backtest() -> int:
    from fennick import backtest as runner, history, model

    models, version = model.load()
    result, audit = runner.run(history.load(), models, version)
    OUT.mkdir(exist_ok=True)
    BACKTEST.write_text(json.dumps(result))
    DECISIONS.write_text("".join(json.dumps(row) + "\n" for row in audit))
    window, lines, live = result["window"], result["lines"], result["live"]
    print(f"Test window {window['from']} to {window['to']}: {window['refunds']:,} refunds, "
          f"{window['frauds']} found to be fraud later.\n")
    print(f"Lines from May: review at risk {lines['review']:.3f} (the riskiest {percent(lines['budget'])}), "
          f"hold at {lines['hold']:.2f}.")
    print(f"Live: {live['stopped']} refunds stopped ({percent(live['review_rate'])}), "
          f"{live['caught']} of {window['frauds']} frauds caught, {live['paid_fraud']} paid.")
    print(f"Held at once: {live['hold']['held']}, of which {live['hold']['fraud']} were fraud "
          f"({percent(live['hold']['precision'])}).\n")
    print(f"{'Each scorer reviews its riskiest 5%':<40}{'caught':>8}{'of':>6}")
    for name, label in (("model", "Decision model"), ("hand_rule", "Hand rule")):
        score = result["at_budget"][name]
        print(f"  {label:<38}{score['caught']:>8}{score['frauds']:>6}")
    calibrated, raw = result["calibration"]["calibrated"], result["calibration"]["raw"]
    print(f"\nCalibration error: {raw['error']:.4f} raw, {calibrated['error']:.4f} calibrated. "
          f"Brier: {raw['brier']:.4f} raw, {calibrated['brier']:.4f} calibrated.")
    print(f"One audit row per decision: {DECISIONS}")
    return 0


def case(refund_id: str) -> int:
    if not DECISIONS.exists():
        raise SystemExit("No decisions yet. Run `uv run refunds backtest` first.")
    row = next((json.loads(line) for line in DECISIONS.read_text().splitlines()
                if json.loads(line)["refund_id"] == refund_id), None)
    if not row:
        print(f"No decision for {refund_id} in the test window.")
        return 1
    print(json.dumps(row, indent=1))
    found = next(item for item in json.loads(BACKTEST.read_text())["queue"] if item["refund_id"] == refund_id)
    print(f"\nFound later: {found['found_later'].replace('_', ' ')}" + (f" on {found['found_on']}" if found["found_on"] else ""))
    return 0


def ask_llm() -> int:
    from fennick import ask_llm as baseline, backtest as runner, history, model
    from fennick.config import model_client

    rows = history.load()
    models, _ = model.load()
    client, model_name = model_client()
    picked = baseline.sample(model.window(rows, "test"))
    print(f"Asking {model_name} for a 0 to 100 fraud score on {len(picked):,} test-window refunds.")
    asked = asyncio.run(baseline.score_all(client, model_name, picked, lambda done, total: (
        print(f"  {done} of {total} batches scored", flush=True) if done % 10 == 0 or done == total else None)))
    compared = runner.compare_with_llm(rows, models, asked)
    OUT.mkdir(exist_ok=True)
    LLM.write_text(json.dumps(compared))
    print(f"\nSame {compared['refunds']:,} refunds, {compared['frauds']} fraud, each scorer reviews its riskiest 5%:")
    for name, label in (("model", "Decision model"), ("llm", "LLM score"), ("hand_rule", "Hand rule")):
        score = compared["at_budget"][name]
        brier = compared["calibration"].get(name, {}).get("brier")
        print(f"  {label:<18}caught {score['caught']:>3} of {score['frauds']}"
              + (f"   Brier {brier:.4f}" if brier is not None else ""))
    usage = compared["usage"]
    print(f"\n{compared['calls']} calls, {usage['prompt_tokens']:,} tokens in, {usage['completion_tokens']:,} out, "
          f"${usage['cost']:.4f}. Refunds the LLM skipped: {compared['missing']}.")
    return 0


def main() -> None:
    from fennick.config import PORT

    parser = argparse.ArgumentParser(prog="refunds")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("history", help="write the seeded refund history to data/refunds.csv")
    sub.add_parser("train", help="train on January to April, calibrate on May")
    sub.add_parser("backtest", help="decide every June and July refund and grade it on fraud found later")
    sub.add_parser("case").add_argument("refund", help="a refund id from the test window, like FM-271207")
    sub.add_parser("ask-llm", help="ask an LLM for a 0 to 100 score on a sample, the naive baseline")
    sub.add_parser("serve").add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if args.command == "serve":
        from fennick.serve import main as serve

        sys.exit(serve(args.port))
    commands = {"history": history, "train": train, "backtest": backtest, "ask-llm": ask_llm}
    sys.exit(case(args.refund) if args.command == "case" else commands[args.command]())
