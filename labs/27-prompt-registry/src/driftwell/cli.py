"""`driftwell`: move the prompt into Langfuse, propose and score versions, promote, roll back, run the bot."""

import argparse
import difflib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

TIMEOUT = 60  # seconds per model call
LIVE_TRAFFIC = ["G02", "G05", "G11", "G09", "G16", "G07"]  # golden conversations the running bot replays


def registry():
    from langfuse import Langfuse

    from driftwell.config import langfuse_settings

    settings = langfuse_settings()
    return Langfuse(public_key=settings["LANGFUSE_PUBLIC_KEY"], secret_key=settings["LANGFUSE_SECRET_KEY"],
                    base_url=settings["LANGFUSE_BASE_URL"])


def model():
    from openai import OpenAI

    from driftwell.config import llm_settings

    base_url, api_key, model_name = llm_settings()
    return OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2), model_name


def reset() -> int:
    from driftwell.registry import delete_all
    from driftwell.release import ReleaseLog, Scores

    client = registry()
    delete_all(client)
    ReleaseLog().clear()
    Scores().clear()
    print("Registry cleared: no versions of support-bot, no scores, empty release log.")
    return 0


def import_prompt() -> int:
    from driftwell.naive import PROMPT_IN_CODE
    from driftwell.registry import import_from_code
    from driftwell.release import ReleaseLog

    number = import_from_code(registry(), PROMPT_IN_CODE)
    ReleaseLog().add(at=time.time(), kind="import", label="production", came_from=None, to=number,
                     by="engineering", score=None, reason="moved out of src/driftwell/naive.py", served_after=None)
    print(f"Imported the prompt from the code as version {number}, labelled production.")
    return 0


def propose(path: str, message: str, author: str) -> int:
    from driftwell.registry import propose as save_version
    from driftwell.release import ReleaseLog

    number = save_version(registry(), Path(path).read_text(), message, author)
    ReleaseLog().add(at=time.time(), kind="propose", label="staging", came_from=None, to=number, by=author,
                     score=None, reason=message, served_after=None)
    print(f"Saved version {number} on staging, by {author}: {message}")
    return 0


def show_versions() -> int:
    from driftwell.registry import versions
    from driftwell.release import Scores, gate

    scores = Scores()
    for version in versions(registry()):
        record = scores.of(version.text)
        scored = f"{record['passed']} of {record['total']}" if record else "not scored"
        verdict = "blocked" if gate(record) else "may ship"
        print(f"v{version.number:<3} {','.join(version.labels) or '-':<20} {scored:<12} {verdict:<9} {version.message}")
    return 0


def evaluate(again: bool) -> int:
    from driftwell.bot import SupportBot
    from driftwell.golden import cases, score
    from driftwell.prompt_source import PromptSource
    from driftwell.registry import versions
    from driftwell.release import Scores, gate, needed

    client, scores = registry(), Scores()
    llm, model_name = model()
    golden = cases()
    print(f"Golden set: {len(golden)} conversations. Bar: {needed(len(golden))} must pass. Model: {model_name}\n")
    total_cost = 0.0
    for version in versions(client):
        record = scores.of(version.text)
        if record is None or again:
            bot = SupportBot(PromptSource(client, version=version.number), llm, model_name, tracer=client)
            record = score(golden, lambda case: bot.reply(case["message"], case["customer"]))
            scores.put(version.text, version.number, record)
            total_cost += record["cost"]
        verdict = "blocked" if gate(record) else "may ship"
        print(f"v{version.number}  {record['passed']} of {record['total']} pass  {verdict}  {version.message}")
        for row in record["rows"]:
            if row["failed"]:
                print(f"     {row['id']} {row['topic']}: {'; '.join(row['failed'])}")
    client.flush()
    print(f"\nScored on the real model for ${total_cost:.4f}. Each reply is a trace linked to its version in Langfuse.")
    return 0


def promote(number: int, by: str, override: str) -> int:
    from driftwell.release import ReleaseLog, Refused, Scores
    from driftwell.release import promote as move_production

    try:
        event = move_production(registry(), ReleaseLog(), Scores(), number, by, override)
    except Refused as refusal:
        print(f"BLOCKED. {refusal}")
        return 0
    kind = "OVERRIDE by" if event["kind"] == "override" else "Promoted by"
    print(f"{kind} {by}: production moved from v{event['came_from']} to v{event['to']}."
          + (f" Reason: {event['reason']}" if event["reason"] else ""))
    return 0


def diff(older: int, newer: int) -> int:
    from driftwell.registry import versions

    texts = {version.number: version.text for version in versions(registry())}
    for line in difflib.unified_diff(texts[older].splitlines(), texts[newer].splitlines(), f"v{older}", f"v{newer}",
                                     lineterm="", n=0):
        print(line)
    return 0


def ask(message: str) -> int:
    from driftwell.bot import SupportBot
    from driftwell.prompt_source import PromptSource

    client = registry()
    llm, model_name = model()
    answered = SupportBot(PromptSource(client), llm, model_name, tracer=client).reply(message, "Tom")
    client.flush()
    version = f"v{answered['version']}" if answered["version"] else "no version"
    print(f"[{version}, {answered['source']}{', ' + answered['reason'] if answered['reason'] else ''}]")
    print(answered["reply"])
    return 0


def run_bot(every: float, count: int) -> int:
    """The long-running bot: one process, one prompt cache, a reply every `every` seconds. Never restarted."""
    from driftwell.bot import SupportBot
    from driftwell.golden import cases
    from driftwell.prompt_source import PromptSource

    # A stop signal exits cleanly, so the SDK's exit hook sends the last traces before the process ends.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    client = registry()
    llm, model_name = model()
    bot = SupportBot(PromptSource(client), llm, model_name, tracer=client)
    traffic = {case["id"]: case for case in cases()}
    sent = 0
    while count == 0 or sent < count:
        case = traffic[LIVE_TRAFFIC[sent % len(LIVE_TRAFFIC)]]
        answered = bot.reply(case["message"], case["customer"])
        print(json.dumps({"at": answered["at"], "version": answered["version"], "source": answered["source"],
                          "case": case["id"], "reply": answered["reply"]}), flush=True)
        sent += 1
        time.sleep(every)
    client.flush()
    return 0


def watch_rollback(by: str, every: float = 1.0, limit: float = 90.0) -> dict:
    """Roll back while a separate bot process keeps answering, and time the first reply on the old version."""
    from driftwell.release import ReleaseLog, note_reply, rollback

    bot = subprocess.Popen([sys.executable, "-m", "driftwell.cli", "bot", "--every", str(every)],
                           stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    replies: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [replies.put(json.loads(line)) for line in bot.stdout], daemon=True).start()
    try:
        before = replies.get(timeout=limit)
        print(f"  bot  v{before['version']}  {before['case']}  {before['reply'][:90]!r}")
        log = ReleaseLog()
        event = rollback(registry(), log, by)
        print(f"  {time.strftime('%H:%M:%S')}  {by} moved production from v{event['came_from']} to v{event['to']}")
        while time.time() - event["at"] < limit:
            reply = replies.get(timeout=limit)
            print(f"  bot  v{reply['version']}  +{reply['at'] - event['at']:.1f}s  {reply['case']}  {reply['reply'][:70]!r}")
            closed = note_reply(log, reply)
            if closed:
                return closed
        raise SystemExit(f"No reply from v{event['to']} within {limit:.0f} s")
    finally:
        bot.terminate()
        bot.wait()


def main() -> None:
    parser = argparse.ArgumentParser(prog="driftwell")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("reset", help="delete every version of the prompt, its scores and the release log")
    sub.add_parser("import", help="move the prompt from the code into the registry as version 1")
    proposal = sub.add_parser("propose", help="save an edited prompt as a new version on staging")
    proposal.add_argument("file")
    proposal.add_argument("--message", "-m", required=True)
    proposal.add_argument("--by", required=True)
    sub.add_parser("versions", help="every version with its labels and golden set score")
    scoring = sub.add_parser("eval", help="score each unscored version on the golden set with the real model")
    scoring.add_argument("--again", action="store_true", help="score every version again")
    promotion = sub.add_parser("promote", help="move production to a version; below the bar it needs an override")
    promotion.add_argument("version", type=int)
    promotion.add_argument("--by", required=True)
    promotion.add_argument("--override", default="", help="the reason a person ships a version below the bar")
    back = sub.add_parser("rollback", help="move production back while a running bot keeps answering, and time it")
    back.add_argument("--by", required=True)
    changes = sub.add_parser("diff")
    changes.add_argument("older", type=int)
    changes.add_argument("newer", type=int)
    one = sub.add_parser("ask", help="one reply from the production prompt")
    one.add_argument("message")
    running = sub.add_parser("bot", help="the long-running bot, one JSON line per reply")
    running.add_argument("--every", type=float, default=2.0)
    running.add_argument("--count", type=int, default=0)
    from driftwell.config import PORT

    sub.add_parser("serve").add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if args.command == "rollback":
        event = watch_rollback(args.by)
        print(f"\nRolled back to v{event['to']}: the running bot answered from it {event['served_after']} s after "
              "the label moved. No restart, no deploy.")
        sys.exit(0)
    commands = {"reset": reset, "import": import_prompt, "versions": show_versions,
                "eval": lambda: evaluate(args.again), "promote": lambda: promote(args.version, args.by, args.override),
                "propose": lambda: propose(args.file, args.message, args.by),
                "diff": lambda: diff(args.older, args.newer), "ask": lambda: ask(args.message),
                "bot": lambda: run_bot(args.every, args.count)}
    if args.command in commands:
        sys.exit(commands[args.command]())
    from driftwell.serve import main as serve

    sys.exit(serve(args.port))


if __name__ == "__main__":
    main()
