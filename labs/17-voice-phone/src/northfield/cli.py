"""`phone`: record the callers, run the agent worker, ring one call, review all ten, or open the wallboard."""

import argparse
import sys

from northfield.config import OUT


def record() -> int:
    from northfield.voices import record_all

    written = record_all()
    print(f"Recorded {len(written)} caller lines into data/calls." if written else "Every caller line is already recorded.")
    return 0


def show(call: dict) -> None:
    mark = "REACHED" if call["reached"] else "MISSED "
    print(f"  {call['id']}  {mark}  {call['caller']:<15} {call['why']}")
    if call["transfer"]:
        print(f"         to a person ({call['transfer']['reason']}): {call['transfer']['summary']}")
    print(f"         {call['seconds']} s, {call['lines_said']} caller lines, talked over {call['talked_over']} times, "
          f"${call['cost']:.5f}")


def ring(call_id: str) -> int:
    from northfield.review import review_calls

    report = review_calls(only=[call_id])
    for call in report["calls"]:
        for turn in call["turns"]:
            print(f"  {turn['at']:>5}s  {turn['who']:<7}{turn['text']}")
        print()
        show(call)
    return 0


def review() -> int:
    from northfield.review import review_calls

    report = review_calls(on_call=lambda call_id: print(f"  ringing {call_id} ...", flush=True))
    print()
    for call in report["calls"]:
        show(call)
    score = report["score"]
    print(f"\n{score['reached']} of {score['calls']} calls reached their outcome. "
          f"{score['resolved_without_person']} resolved without a person, {len(score['transfers'])} sent to a person: "
          f"{', '.join(transfer['id'] + ' ' + transfer['reason'] for transfer in score['transfers']) or 'none'}.")
    print(f"Talked over the caller {score['talked_over']} times. Model {report['model']}, ${score['cost']:.4f} for all calls.")
    print(f"Every transcript and verdict: {OUT / 'review.json'}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="phone")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("record")
    sub.add_parser("agent")
    sub.add_parser("call").add_argument("call_id")
    sub.add_parser("review")
    sub.add_parser("serve").add_argument("--port", type=int, default=8017)
    args = parser.parse_args()
    if args.command == "record":
        sys.exit(record())
    if args.command == "agent":
        from northfield.agent import run_worker

        sys.exit(run_worker())
    if args.command == "call":
        sys.exit(ring(args.call_id))
    if args.command == "review":
        sys.exit(review())
    from northfield.serve import main as serve

    sys.exit(serve(args.port))


if __name__ == "__main__":
    main()
