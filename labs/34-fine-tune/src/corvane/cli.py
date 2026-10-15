"""`tune`: export the queue's labels, score the baselines, train, gate the model and the cascade, shadow, serve."""

import argparse
import json
import sys
from pathlib import Path

SCORES = Path("out/scores.json")
RELEASE = Path("out/release.json")
QUEUE = Path("out/queue.json")
TIMEOUT = 60  # seconds per model call
NAMES = {"large": "Large model, few-shot", "untuned": "Qwen3 0.6B untuned, few-shot", "tuned": "Qwen3 0.6B tuned"}


def large_model(train: list[dict]):
    from openai import OpenAI

    from corvane.classify import LargeModel, pick_examples
    from corvane.config import llm_settings

    base_url, api_key, model_name = llm_settings()
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2)
    return LargeModel(client, model_name, pick_examples(train))


def splits() -> dict[str, list[dict]]:
    from corvane.labels import DECISIONS, REVIEWED, export, read_jsonl

    return export(read_jsonl(REVIEWED), read_jsonl(DECISIONS))


def load_scores() -> dict:
    return json.loads(SCORES.read_text()) if SCORES.exists() else {}


def save_score(key: str, model_name: str, items: list[dict], predictions, extra: dict | None = None) -> dict:
    from corvane.release import score

    rows = [prediction.as_dict() for prediction in predictions]
    scores = load_scores()
    scores[key] = {"model": model_name, **score(items, rows), **(extra or {}), "predictions": rows}
    SCORES.parent.mkdir(exist_ok=True)
    SCORES.write_text(json.dumps(scores, indent=1))
    return scores[key]


def print_score(key: str, result: dict) -> None:
    print(f"  {NAMES[key]:<30} accuracy {result['accuracy']:.1%} ({result['correct']} of {result['total']}), "
          f"unparseable {result['unparsed']}, ${result['cost_per_thousand']:.4f} per 1,000 items")


def export_labels() -> int:
    from corvane.labels import DECISIONS, class_counts, read_jsonl, write_splits

    parts = splits()
    write_splits(parts)
    decided = len(read_jsonl(DECISIONS))
    print(f"Exported {sum(len(part) for part in parts.values())} reviewed labels "
          f"({decided} from your review decisions, training only).")
    print(f"{len(parts['train'])} train, {len(parts['valid'])} valid, {len(parts['test'])} test. "
          "The test split is never trained or prompted on.\n")
    test_counts = class_counts(parts["test"])
    for category, count in class_counts(parts["train"]).most_common():
        print(f"  {category:<22} train {count:>4}   test {test_counts[category]:>3}")
    return 0


def baseline() -> int:
    from corvane.classify import SmallModel, pick_examples
    from corvane.train import base_model_path

    parts = splits()
    test, train = parts["test"], parts["train"]
    large = large_model(train)
    print(f"Scoring {len(test)} held-out items against the reviewers' labels.\n")
    print_score("large", save_score("large", large.name, test, large.classify(test)))
    untuned = SmallModel(base_model_path(), examples=pick_examples(train))
    predictions = untuned.classify(test)
    print_score("untuned", save_score("untuned", untuned.name, test, predictions, {"seconds": round(untuned.seconds, 1)}))
    return 0


def train_adapter(force: bool) -> int:
    from corvane.train import Recipe, train

    recipe = Recipe()
    print(f"LoRA on Qwen3 0.6B, on a Kaggle GPU: one pass of batch {recipe.batch_size}, at most "
          f"{recipe.max_steps} steps, rank {recipe.rank} on {', '.join(recipe.targets)}.")
    train(recipe, force=force)
    print("The adapter is in out/adapter/, ready for MLX. Run `uv run tune evaluate` next.")
    return 0


def evaluate() -> int:
    from corvane.classify import SmallModel
    from corvane.release import decide_release
    from corvane.train import ADAPTER_DIR, base_model_path

    if not (ADAPTER_DIR / "adapters.safetensors").exists():
        raise SystemExit("No adapter in out/adapter. Run `uv run tune train` first, which trains on Kaggle.")
    test = splits()["test"]
    tuned = SmallModel(base_model_path(), adapter_path=str(ADAPTER_DIR))
    save_score("tuned", tuned.name, test, tuned.classify(test), {"seconds": round(tuned.seconds, 1)})
    scores = load_scores()
    print(f"Held-out test, {len(test)} items the reviewers labelled:\n")
    for key in ("large", "untuned", "tuned"):
        if key in scores:
            print_score(key, scores[key])
    print_release("The tuned model alone", decide_release(scores.get("tuned"), scores.get("large")))
    return 0


def print_release(subject: str, release: dict) -> None:
    print()
    for check in release["checks"]:
        print(f"  {'pass' if check['passed'] else 'FAIL'}  {check['name']}: {check['detail']}")
    print(f"\n{subject}: {release['decision'].upper()}." + (" " + "; ".join(release["reasons"]) + "." if release["reasons"] else ""))


def run_cascade() -> int:
    from corvane.classify import SmallModel
    from corvane.labels import HELDOUT, read_jsonl
    from corvane.release import adapter_card, cascade, decide_cascade, decide_release, pick_threshold, sure
    from corvane.train import ADAPTER_DIR, TRAIN_REPORT, base_model_path

    scores = load_scores()
    if "tuned" not in scores or "large" not in scores:
        raise SystemExit("Run `uv run tune baseline` and `uv run tune evaluate` first: the cascade reuses their answers.")
    valid = splits()["valid"]
    # The threshold comes from the validation split, which the adapter never trained on and the gate never tests on.
    tuned = SmallModel(base_model_path(), adapter_path=str(ADAPTER_DIR))
    valid_predictions = [prediction.as_dict() for prediction in tuned.classify(valid)]
    threshold = pick_threshold(valid_predictions)
    valid_kept = sum(sure(prediction, threshold) for prediction in valid_predictions)
    print(f"Validation, {len(valid)} items: threshold {threshold:.0%} keeps {valid_kept} with the small model.")
    test = read_jsonl(HELDOUT)
    tuned_test, large_test = scores["tuned"]["predictions"], scores["large"]["predictions"]
    release = decide_cascade(test, tuned_test, large_test, threshold)
    truth = {item["id"]: item for item in test}
    # The items sent up, kept with the small model's guess and the reviewer's label, so the page can show why.
    routed = [{key: truth[answer["id"]][key] for key in ("id", "descriptor", "amount")}
              | {"truth": truth[answer["id"]]["label"], "large": answer["label"], "reason": answer["reason"],
                 "small": answer["small"]["label"], "confidence": answer["small"]["confidence"]}
              for answer in cascade(tuned_test, large_test, threshold) if answer["model"] == "large"]
    plain = decide_release(scores["tuned"], scores["large"])
    RELEASE.write_text(json.dumps({**release, "plain": plain, "routed_items": routed,
                                   "valid": {"total": len(valid), "kept": valid_kept}}, indent=1))
    trained = json.loads(TRAIN_REPORT.read_text()) if TRAIN_REPORT.exists() else None
    card = adapter_card(trained, scores["tuned"], scores["large"], release)
    (ADAPTER_DIR / "card.json").write_text(json.dumps(card, indent=1))
    print(f"Held-out test: {release['kept']} of {release['total']} labelled by the small model, "
          f"{release['routed']} sent to the large model.")
    print_release("The cascade", release)
    return 0


def shadow() -> int:
    from corvane.classify import SmallModel
    from corvane.labels import INCOMING, read_jsonl
    from corvane.release import route
    from corvane.train import ADAPTER_DIR, base_model_path

    if not RELEASE.exists():
        raise SystemExit("No threshold yet. Run `uv run tune cascade` first.")
    threshold = json.loads(RELEASE.read_text())["threshold"]
    incoming = read_jsonl(INCOMING)
    tuned = SmallModel(base_model_path(), adapter_path=str(ADAPTER_DIR)).classify(incoming)
    routed = [route(item, prediction.as_dict(), threshold) for item, prediction in zip(incoming, tuned)]
    # Only the items the small model is unsure of are paid for at the large model's price.
    sent_up = [row for row in routed if row["route"] == "large"]
    for row, answer in zip(sent_up, large_model(splits()["train"]).classify(sent_up)):
        row["large"] = answer.as_dict()
    QUEUE.write_text(json.dumps(routed, indent=1))
    print(f"This week's items: {len(routed) - len(sent_up)} of {len(routed)} labelled by the small model, "
          f"{len(sent_up)} sent to the large model. Nothing waits for a person.\n")
    for row in sent_up[:8]:
        print(f"  {row['id']}  {row['descriptor']:<38} large: {row['large']['label']}. {row['reason']}")
    return 0


def review(item_id: str, label: str) -> int:
    from corvane.labels import DECISIONS, read_jsonl, write_jsonl
    from corvane.release import decision_row

    queue = json.loads(QUEUE.read_text()) if QUEUE.exists() else []
    item = next((row for row in queue if row["id"] == item_id), None)
    if not item:
        raise SystemExit(f"{item_id} is not in out/queue.json. Run `uv run tune shadow` first.")
    decisions = [row for row in read_jsonl(DECISIONS) if row["id"] != item_id]
    write_jsonl(DECISIONS, decisions + [decision_row(item, label, "you")])
    print(f"{item_id} labelled {label}. {len(decisions) + 1} decisions join the next training set.")
    return 0


def main() -> None:
    from corvane.config import PORT
    from corvane.labels import CATEGORIES

    parser = argparse.ArgumentParser(prog="tune")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("export", help="split the review queue's labels and write chat examples")
    sub.add_parser("baseline", help="score the large model and the untuned small model on the test split")
    sub.add_parser("train", help="LoRA train the small model on a Kaggle GPU and bring the adapter back") \
        .add_argument("--force", action="store_true", help="train again even when this data and recipe already did")
    sub.add_parser("evaluate", help="score the tuned model and gate it alone")
    sub.add_parser("cascade", help="set the threshold on validation and gate the small-then-large cascade")
    sub.add_parser("shadow", help="label this week's items, sending the unsure ones to the large model")
    decide = sub.add_parser("review", help="record a reviewer's label for a queued item")
    decide.add_argument("item")
    decide.add_argument("label", choices=CATEGORIES)
    sub.add_parser("serve").add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if args.command == "export":
        sys.exit(export_labels())
    if args.command == "baseline":
        sys.exit(baseline())
    if args.command == "train":
        sys.exit(train_adapter(args.force))
    if args.command == "evaluate":
        sys.exit(evaluate())
    if args.command == "cascade":
        sys.exit(run_cascade())
    if args.command == "shadow":
        sys.exit(shadow())
    if args.command == "review":
        sys.exit(review(args.item, args.label))
    from corvane.serve import main as serve

    sys.exit(serve(args.port))
