"""`fleet`: build the sandbox image, run the laptop baseline, check isolation, run the queue, review, serve."""

import argparse
import json
import sys
import time

from fernhill.config import ISSUES_FILE, OUT, PLANTED_SECRET, PORT

RUN_FILE = OUT / "run.json"
LAPTOP_FILE = OUT / "laptop.json"
TIMEOUT = 120  # seconds per model call


def issues(wanted: list[str] | None = None) -> list[dict]:
    every = [json.loads(line) for line in ISSUES_FILE.read_text().splitlines() if line.strip()]
    if not wanted:
        return every
    unknown = set(wanted) - {issue["id"] for issue in every}
    if unknown:
        raise SystemExit(f"No such issue: {', '.join(sorted(unknown))}")
    return [issue for issue in every if issue["id"] in wanted]


def model():
    from openai import OpenAI

    from fernhill.config import llm_settings

    base_url, api_key, model_name = llm_settings()
    return OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2), model_name


def build() -> int:
    from fernhill.sandbox import build_image, docker_available

    if not docker_available():
        raise SystemExit("Docker is not running. Start Docker Desktop (or any Docker engine) and try again.")
    started = time.monotonic()
    print(f"Built {build_image(OUT)} in {time.monotonic() - started:.0f}s")
    return 0


def laptop(wanted: list[str]) -> int:
    from fernhill.laptop import laptop_run

    client, model_name = model()
    print(f"The agent on the developer's laptop, in their checkout, one issue at a time. Model: {model_name}\n")
    report = laptop_run(issues(wanted), client, model_name, OUT)
    print("\nWhat the agent's shell could reach on the laptop:")
    for label, result in report["probes"].items():
        print(f"  {label:<38} {'REACHED' if result['reached'] else 'blocked'}")
    print(f"\nThe laptop was busy for {report['blocked_seconds']:.0f}s on {len(report['runs'])} issues. "
          f"Cost ${report['cost']:.4f}.")
    LAPTOP_FILE.parent.mkdir(exist_ok=True)
    LAPTOP_FILE.write_text(json.dumps(report, indent=1))
    return 0


def start_stack(key: str, base_url: str, model_name: str):
    """The image, the internal network and the egress proxy: everything sessions share."""
    from fernhill.orchestrator import Egress, docker_sandboxes
    from fernhill.sandbox import build_image, ensure_network

    image = build_image(OUT)
    ensure_network()
    egress = Egress()
    egress.start(image, base_url, key, model_name)
    return image, egress, *docker_sandboxes(image)


def isolation_report() -> dict:
    """The same probes as the laptop, asked from inside a sandbox, plus how that sandbox was started."""
    import os

    from fernhill.config import DEFAULT_BASE_URL, DEFAULT_MODEL, load_dotenv
    from fernhill.laptop import PROBES, plant, reached
    from fernhill.sandbox import remove_all

    load_dotenv()
    # The token sits in this host process's environment; HOME stays real so the Docker client finds its config.
    os.environ["FERNHILL_DEPLOY_TOKEN"] = plant(OUT / "laptop" / "home")["FERNHILL_DEPLOY_TOKEN"]
    try:
        _, egress, agent_sandbox, _ = start_stack(os.environ.get("LLM_API_KEY") or "no-key", DEFAULT_BASE_URL,
                                                  os.environ.get("LLM_MODEL") or DEFAULT_MODEL)
        workspace = OUT / "sessions" / "isolation"
        workspace.mkdir(parents=True, exist_ok=True)
        sandbox = agent_sandbox(workspace, "isolation", {"LLM_BASE_URL": egress.inside_url})
        probes = {label: reached(sandbox.exec(["sh", "-c", command], timeout=30).output, label)
                  for label, command in PROBES.items()}
        proxy_probe = ("python -c \"import urllib.request as web; web.urlopen('http://lab32-egress:8321/v1/models')\" "
                       "2>&1 | tail -1")
        proxy = sandbox.exec(["sh", "-c", proxy_probe], timeout=30).output.strip()
        started = sandbox.inspect()
    finally:
        remove_all()
    return {"probes": probes, "proxy": proxy, "started": started,
            "token_in_env": PLANTED_SECRET in json.dumps(started["env"])}


def isolation() -> int:
    report = isolation_report()
    print("The same probes, from inside a sandbox:")
    for label, did_reach in report["probes"].items():
        print(f"  {label:<38} {'REACHED' if did_reach else 'blocked'}")
    print(f"  {'the proxy, for anything but the model':<38} {report['proxy']}")
    started = report["started"]
    print(f"\nStarted with: read-only root {started['read_only']}, memory {started['memory'] // 2**20} MB, "
          f"{started['nano_cpus'] / 1e9:g} CPU, network {started['network']}, user {started['user']}, "
          f"capabilities dropped {started['cap_drop']}")
    print(f"Host folders mounted: {len(started['mounts'])} (the session's own copy of the repository)")
    print(f"Planted token in the sandbox's environment: {'YES' if report['token_in_env'] else 'no'}")
    return 0


def docker_fleet(forge, parallel: int, max_seconds: float, max_dollars: float):
    """A fleet on Docker sandboxes behind the egress proxy. Returns it with the cleanup to call after."""
    import os

    from fernhill.config import llm_settings
    from fernhill.orchestrator import Fleet
    from fernhill.sandbox import remove_all

    base_url, key, model_name = llm_settings()
    # The deploy token sits in this host process's environment, as it would on a build server.
    os.environ["FERNHILL_DEPLOY_TOKEN"] = PLANTED_SECRET
    try:
        _, egress, agent_sandbox, ci_sandbox = start_stack(key, base_url, model_name)
    except Exception:
        remove_all()
        raise
    fleet = Fleet(forge, egress, agent_sandbox, ci_sandbox, model_name, OUT, parallel, max_seconds, max_dollars)
    return fleet, remove_all


def save_run(forge, sessions: list, model_name: str, wall: float, queue: list[dict], fresh: bool) -> dict:
    """Keep earlier sessions for issues this run did not touch, then score every pull request on the forge."""
    from fernhill.orchestrator import scoreboard

    previous = json.loads(RUN_FILE.read_text())["sessions"] if RUN_FILE.exists() and not fresh else []
    touched = {issue["id"] for issue in queue}
    records = [*[old for old in previous if old["issue"]["id"] not in touched], *[one.public() for one in sessions]]
    saved = {"model": model_name, "sessions": records, "board": scoreboard(records, forge.pulls, wall)}
    RUN_FILE.parent.mkdir(exist_ok=True)
    RUN_FILE.write_text(json.dumps(saved, indent=1))
    return saved


def run(wanted: list[str], fresh: bool, parallel: int, max_seconds: float, max_dollars: float) -> int:
    from fernhill.forge import Forge

    forge = Forge(OUT / "forge")
    if fresh or not forge.bare.exists():
        forge.reset()
    queue = issues(wanted)
    fleet, cleanup = docker_fleet(forge, parallel, max_seconds, max_dollars)
    print(f"{len(queue)} issues, {parallel} sandboxes at a time, model {fleet.model}. "
          f"Budget per session: {max_seconds:.0f}s and ${max_dollars:g}.\n")
    started = time.monotonic()
    try:
        sessions = fleet.run(queue)
    finally:
        cleanup()
    wall = time.monotonic() - started
    for session in sessions:
        pull = forge.pull(session.pull) if session.pull else None
        detail = (f"PR #{pull['number']}  CI {pull['ci']['status']}" if pull else f"{session.status}: {session.reason}")
        print(f"  {session.issue['id']}  {session.seconds():>6.1f}s  ${session.cost:.4f}  {detail}")
    save_run(forge, sessions, fleet.model, wall, queue, fresh)
    reached_green = sum(forge.pull(session.pull)["ci"]["status"] == "passed" for session in sessions if session.pull)
    print(f"\n{reached_green} of {len(sessions)} issues reached green CI. Pull requests opened: "
          f"{sum(session.pull is not None for session in sessions)}. Wall time {wall:.0f}s. "
          f"Cost ${sum(session.cost for session in sessions):.4f}.")
    print(f"Planted host token seen in any sandbox: {sum(session.secret_seen for session in sessions)}")
    return 0


def labelled_review(reviewer: str) -> int:
    """Stands in for the reviewer in a walkthrough: merge when CI is green and the held-out test passes."""
    from fernhill.forge import Forge, MergeRefused
    from fernhill.orchestrator import scoreboard

    forge = Forge(OUT / "forge")
    kinds = {issue["id"]: issue["kind"] for issue in issues()}
    for pull in forge.pulls:
        if pull["state"] != "open":
            continue
        held_out = (pull["ci"].get("acceptance") or {}).get("passed")
        reason = ("the issue asks for something that does not belong in a pull request"
                  if kinds.get(pull["issue"]["id"]) == "refuse" else
                  "the issue's held-out test still fails" if held_out is False else None)
        try:
            if reason:
                forge.close(pull["number"], reviewer, reason)
                print(f"  #{pull['number']} {pull['issue']['id']}  closed: {reason}")
                continue
            forge.merge(pull["number"], reviewer)
            print(f"  #{pull['number']} {pull['issue']['id']}  merged by {reviewer}")
        except MergeRefused as refusal:
            forge.close(pull["number"], reviewer, str(refusal))
            print(f"  #{pull['number']} {pull['issue']['id']}  merge refused, closed: {refusal}")
    saved = json.loads(RUN_FILE.read_text()) if RUN_FILE.exists() else {"sessions": [], "board": {"wall_seconds": 0}}
    board = scoreboard(saved["sessions"], forge.pulls, saved["board"]["wall_seconds"])
    RUN_FILE.write_text(json.dumps({**saved, "board": board}, indent=1))
    print(f"\nMerged {board['merged']} of {board['pulls']} agent pull requests ({board['merged_share']:.0%}). "
          f"CI pass rate {board['ci_pass_rate']:.0%}. Cost per pull request ${board['cost_per_pull'] or 0:.4f}.")
    # Each merge was green on its own branch. Two green branches can still break each other, so check main.
    print(f"CI on main after the merges: {main_ci(forge)['status']}")
    return 0


def main_ci(forge) -> dict:
    from fernhill.ci import run_ci
    from fernhill.orchestrator import docker_sandboxes
    from fernhill.sandbox import build_image, remove_all

    try:
        return run_ci(forge, "main", OUT / "ci" / "main", docker_sandboxes(build_image(OUT))[1])
    finally:
        remove_all()


def decide(action: str, number: int, reviewer: str, reason: str) -> int:
    from fernhill.forge import Forge, MergeRefused

    forge = Forge(OUT / "forge")
    try:
        record = forge.merge(number, reviewer) if action == "merge" else forge.close(number, reviewer, reason)
    except MergeRefused as refusal:
        print(f"Refused: {refusal}")
        return 1
    print(f"#{number} {record['state']} by {reviewer}")
    return 0


def down() -> int:
    from fernhill.sandbox import remove_all

    print(f"Removed {remove_all()} lab32- containers and the sandbox network.")
    return 0


def main() -> None:
    from fernhill.config import PARALLEL_SESSIONS, SESSION_DOLLARS, SESSION_SECONDS

    parser = argparse.ArgumentParser(prog="fleet")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("image", help="build the sandbox image; no key needed")
    on_laptop = sub.add_parser("laptop", help="the baseline: the agent in the developer's checkout")
    on_laptop.add_argument("issues", nargs="+")
    sub.add_parser("isolation", help="probe a sandbox from inside; no key needed")
    queue = sub.add_parser("run", help="run issues in sandboxes and open pull requests")
    queue.add_argument("issues", nargs="*", help="issue ids; all of data/issues.jsonl when left out")
    queue.add_argument("--fresh", action="store_true", help="start the forge again from data/repo")
    queue.add_argument("--parallel", type=int, default=PARALLEL_SESSIONS)
    queue.add_argument("--max-seconds", type=float, default=SESSION_SECONDS)
    queue.add_argument("--max-dollars", type=float, default=SESSION_DOLLARS)
    review = sub.add_parser("review", help="merge or close every open pull request, by the issue labels")
    review.add_argument("--reviewer", required=True)
    for action in ("merge", "close"):
        one = sub.add_parser(action)
        one.add_argument("number", type=int)
        one.add_argument("--reviewer", required=True)
        one.add_argument("--reason", default="closed by the reviewer")
    sub.add_parser("down", help="remove every lab32- container")
    sub.add_parser("serve").add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if args.command == "image":
        sys.exit(build())
    if args.command == "laptop":
        sys.exit(laptop(args.issues))
    if args.command == "isolation":
        sys.exit(isolation())
    if args.command == "run":
        sys.exit(run(args.issues, args.fresh, args.parallel, args.max_seconds, args.max_dollars))
    if args.command == "review":
        sys.exit(labelled_review(args.reviewer))
    if args.command in ("merge", "close"):
        sys.exit(decide(args.command, args.number, args.reviewer, args.reason))
    if args.command == "down":
        sys.exit(down())
    from fernhill.serve import main as serve

    sys.exit(serve(args.port))

