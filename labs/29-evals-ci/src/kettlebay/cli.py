"""`kettlebay`: run the release check on a pull request's release, re-score saved runs, compare the
two gates, record an override, or open the check page."""

import argparse
import json
import os
import sys
from pathlib import Path

RUNS = Path("runs")
LIVE = Path("releases/live.json")


def model_client():
    from openai import OpenAI

    from kettlebay.config import TIMEOUT, llm_settings

    base_url, api_key = llm_settings()
    return OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2)


def run_path(name: str) -> Path:
    return RUNS / f"{name}.json"


def save_run(run: dict) -> None:
    RUNS.mkdir(exist_ok=True)
    run_path(run["release"]).write_text(json.dumps(run, indent=1))


def load_run(name: str) -> dict | None:
    path = run_path(name)
    return json.loads(path.read_text()) if path.exists() else None


def print_verdict(result, live_run: dict, candidate_run: dict) -> None:
    mode = "answers and trajectories" if result.with_trajectory else "answers only"
    print(f"\n{'case':<6}{'live':<7}{'change':<8}{'status':<22}why ({mode})")
    for row in result.rows:
        live, candidate = row["live"], row["candidate"]
        why = ""
        failing = [score for score in candidate["scores"] if not (score["answer_ok"] and (score["trajectory_ok"] or not result.with_trajectory))]
        if row["status"] in ("regressed", "unsafe", "error", "failing on live too") and failing:
            first = failing[0]
            reasons = first["answer_problems"] + (first["unsafe"] if result.with_trajectory else [])
            if result.with_trajectory and not reasons and first["trajectory_score"] < 1:
                reasons = [first["trajectory_reason"]]
            why = "; ".join(reasons)[:110]
        if row["status"] == "missing":
            why = "no result for this golden case"
        print(f"{row['id']:<6}{live['passes']}/{live['runs']:<5}{candidate['passes']}/{candidate['runs']:<6}"
              f"{row['status']:<22}{why}")


def check(path: str, fresh_live: bool, repeats: int) -> int:
    """What CI runs on a pull request: the golden set on live and on the change, then the gate."""
    from kettlebay.assistant import load_golden, load_release
    from kettlebay.config import check_priced
    from kettlebay.gate import find_override, needed, run_release, summary_line, verdict
    from kettlebay.store import load_store

    golden, store_data = load_golden(), load_store()
    live, candidate = load_release(LIVE), load_release(path)
    check_priced(live.model)
    check_priced(candidate.model)
    client = model_client()
    print(f"Release check for {candidate.name}: {candidate.spec['title']} ({candidate.model}).")
    print(f"{len(golden)} golden cases, {repeats} runs each; a case passes in {needed(repeats)} of {repeats} runs.")
    live_run = load_run("live")
    if fresh_live or not live_run or live_run["fingerprint"] != live.fingerprint or live_run["repeats"] != repeats:
        live_run = run_release(live, golden, store_data, client, repeats)
        failed = sum(bool(result.get("error")) for results in live_run["cases"].values() for result in results)
        if failed:
            # A broken baseline would block every release after it, so it is used for this check only.
            print(f"live: {failed} runs failed; not saved as the baseline")
        else:
            save_run(live_run)
        print(f"live: ran in {live_run['seconds']}s, cost ${live_run['cost']:.4f}")
    else:
        print(f"live: reusing the run made at {live_run['made_at']} for this exact live release")
    if candidate.name == "live":
        candidate_run = live_run
    else:
        candidate_run = run_release(candidate, golden, store_data, client, repeats)
        save_run(candidate_run)
        print(f"{candidate.name}: ran in {candidate_run['seconds']}s, cost ${candidate_run['cost']:.4f}")
    result = verdict(golden, live_run, candidate_run)
    print_verdict(result, live_run, candidate_run)
    line = summary_line(result)
    print("\n" + line)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as step_summary:
            step_summary.write(f"### Kettlebay Assist release check\n\n{line}\n")
    if not result.blocked:
        return 0
    override = None if result.broken else find_override(candidate.fingerprint)
    if override:
        print(f"overridden by {override['by']} at {override['at']}: {override['reason']}")
        return 0
    return 1


def gate_saved(name: str, answers_only: bool) -> int:
    """Re-score saved runs with no model calls. A run made for an older version of the release is
    refused: it does not describe what would ship."""
    from kettlebay.assistant import load_golden, load_release
    from kettlebay.gate import summary_line, verdict

    live_run, candidate_run = load_run("live"), load_run(name)
    if not live_run or not candidate_run:
        print(f"blocked: no saved run for {'live' if not live_run else name}. Run kettlebay check first.")
        return 1
    if candidate_run["fingerprint"] != load_release(name).fingerprint:
        print(f"blocked: runs/{name}.json was made for an older version of {name}. Run kettlebay check again.")
        return 1
    result = verdict(load_golden(), live_run, candidate_run, with_trajectory=not answers_only)
    print_verdict(result, live_run, candidate_run)
    print("\n" + summary_line(result))
    return 1 if result.blocked else 0


def report() -> int:
    """Both gates over every saved release run: which changes each one would have stopped."""
    from kettlebay.assistant import load_golden
    from kettlebay.gate import verdict

    golden, live_run = load_golden(), load_run("live")
    if not live_run:
        print("No saved live run. Run kettlebay check first.")
        return 1
    names = sorted(path.stem for path in RUNS.glob("*.json") if path.stem != "live")
    print(f"{'release':<18}{'answers only':<16}{'answers + trajectories':<24}cost")
    caught = {"answers": 0, "both": 0}
    for name in names:
        candidate_run = load_run(name)
        answers = verdict(golden, live_run, candidate_run, with_trajectory=False)
        both = verdict(golden, live_run, candidate_run, with_trajectory=True)
        caught["answers"] += answers.blocked
        caught["both"] += both.blocked
        print(f"{name:<18}{'blocked' if answers.blocked else 'passed':<16}{'blocked' if both.blocked else 'passed':<24}"
              f"${candidate_run['cost']:.4f}")
    print(f"\nReleases blocked before release: {caught['answers']} of {len(names)} with answers only, "
          f"{caught['both']} of {len(names)} with answers and trajectories.")
    return 0


# A change to any of these can change every release's behaviour, so every release is checked again.
SHARED = ("src/", "data/", "releases/live.json", "prompts/live.txt", "tools/live.json", "pyproject.toml", "uv.lock")


def changed(base: str) -> int:
    """The release files a pull request touches, one per line, for CI to check. A release counts as
    touched when its own file, its prompt or its tool descriptions changed."""
    import subprocess

    # --relative keeps paths relative to this folder, so it works as its own repo or inside another.
    files = set(subprocess.run(["git", "diff", "--relative", "--name-only", f"{base}...HEAD"], capture_output=True,
                               text=True, check=True).stdout.split())
    touched = releases_touched(files)
    print("\n".join(touched) if touched else f"No release changed against {base}.")
    return 0


def releases_touched(files: set[str]) -> list[str]:
    everything = any(file.startswith(SHARED) for file in files)
    touched = []
    for path in sorted(Path("releases").glob("*.json")):
        spec = json.loads(path.read_text())
        if path.stem != "live" and (everything or {str(path), spec["prompt"], spec["tools"]} & files):
            touched.append(str(path))
    return touched


def override(name: str, by: str, reason: str) -> int:
    from kettlebay.assistant import load_golden, load_release
    from kettlebay.gate import record_override, verdict

    live_run, candidate_run = load_run("live"), load_run(name)
    release = load_release(name)
    if not live_run or not candidate_run or candidate_run["fingerprint"] != release.fingerprint:
        print(f"No current run for {name}. Run kettlebay check first.")
        return 1
    try:
        entry = record_override(verdict(load_golden(), live_run, candidate_run), release.fingerprint, by, reason)
    except ValueError as error:
        print(f"not recorded: {error}")
        return 1
    print(f"override recorded for {name} by {entry['by']}, waiving {', '.join(entry['waived'])}")
    return 0


def main() -> None:
    from kettlebay.config import PORT, REPEATS

    parser = argparse.ArgumentParser(prog="kettlebay")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("check", help="run the golden set on live and on a release, then gate it (needs a key)")
    one.add_argument("release", help="a release file, such as releases/faster-writes.json")
    one.add_argument("--fresh-live", action="store_true", help="rerun live even when a matching run is saved")
    one.add_argument("--repeats", type=int, default=REPEATS)
    saved = sub.add_parser("gate", help="re-score saved runs with no model calls")
    saved.add_argument("release")
    saved.add_argument("--answers-only", action="store_true", help="the naive gate: read the replies, not the tool calls")
    sub.add_parser("report", help="both gates over every saved run")
    sub.add_parser("changed", help="the releases a pull request touches, for CI").add_argument("--base", default="origin/main")
    waive = sub.add_parser("override", help="ship a blocked release anyway, with a written reason")
    waive.add_argument("release")
    waive.add_argument("--by", required=True)
    waive.add_argument("--reason", required=True)
    sub.add_parser("serve", help="the check page").add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if args.command == "check":
        sys.exit(check(args.release, args.fresh_live, args.repeats))
    if args.command == "gate":
        sys.exit(gate_saved(Path(args.release).stem, args.answers_only))
    if args.command == "report":
        sys.exit(report())
    if args.command == "changed":
        sys.exit(changed(args.base))
    if args.command == "override":
        sys.exit(override(Path(args.release).stem, args.by, args.reason))
    from kettlebay.serve import main as serve

    sys.exit(serve(args.port))
