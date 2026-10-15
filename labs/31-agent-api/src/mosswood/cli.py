"""`assistant`: start the stack, ask through the gateway, run the load test, read the gateway's log."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from mosswood import gateway, gatewaylog
from mosswood.agent import Ledger
from mosswood.client import AGENT, GATEWAY, stream_answer, whole_answer
from mosswood.config import AGENT_PORT, ENVOY_IMAGE, OUT, PAGE_PORT, STUB_PORT, llm_settings, load_dotenv, stub_settings
from mosswood.tokens import issue
from mosswood.workspace import tenant, users

RUN = OUT / "run"
PIDS = RUN / "pids.json"
LOAD = OUT / "load"
STUB = f"http://127.0.0.1:{STUB_PORT}"
DEFAULT_PERSON = "pemberton/priya.raman"


def get_json(url: str, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except (OSError, ValueError):
        return None


def wait_for(url: str, seconds: float = 20.0) -> dict | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = get_json(url)
        if found is not None:
            return found
        time.sleep(0.2)
    return None


def pids() -> dict:
    return json.loads(PIDS.read_text()) if PIDS.exists() else {}


def spawn(name: str, *args: str) -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    log = (RUN / f"{name}.log").open("a")
    process = subprocess.Popen([sys.executable, "-m", "mosswood.cli", *args], stdout=log, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, start_new_session=True, env=os.environ.copy())
    return process.pid


def stop_pid(process_id: int | None) -> None:
    if not process_id:
        return
    try:
        os.kill(process_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(50):
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.kill(process_id, signal.SIGKILL)
    except ProcessLookupError:
        pass


def person(spec: str) -> tuple[str, str]:
    tenant_id, _, user_id = spec.partition("/")
    if (tenant_id, user_id) not in users():
        raise SystemExit(f"No user {spec!r}. Use tenant/user, like {DEFAULT_PERSON}.")
    return tenant_id, user_id


def logged(request_id: str, seconds: float = 3.0) -> dict | None:
    """Find one request in the gateway's log. Envoy flushes it every 250 ms, so wait a moment."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for row in gatewaylog.rows():
            if row.get("request_id") == request_id:
                return row
        time.sleep(0.2)
    return None


def ledger_row(request_id: str, seconds: float = 3.0) -> dict | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for row in Ledger().rows():
            if row.get("request_id") == request_id:
                return row
        time.sleep(0.2)
    return None


def cmd_agent(args) -> int:
    import uvicorn
    from openai import AsyncOpenAI

    from mosswood.agent import create_app
    from mosswood.config import IDLE_SECONDS

    base_url, key, model = llm_settings() if args.upstream == "live" else stub_settings()

    def client():
        import httpx
        return AsyncOpenAI(base_url=base_url, api_key=key, max_retries=0,
                           timeout=httpx.Timeout(IDLE_SECONDS, connect=5.0))

    uvicorn.run(create_app(client, model), host="127.0.0.1", port=AGENT_PORT, log_level="warning")
    return 0


def cmd_stub(_args) -> int:
    import uvicorn

    from mosswood.stub_model import create_stub
    uvicorn.run(create_stub(), host="127.0.0.1", port=STUB_PORT, log_level="warning")
    return 0


def cmd_up(args) -> int:
    load_dotenv()
    model = llm_settings()[2] if args.upstream == "live" else stub_settings()[2]
    # Start everything fresh, so the gateway reads today's limits and nothing from another folder is reused.
    running = pids()
    for name in ("agent", "stub"):
        stop_pid(running.pop(name, None))
    for url, port in ((f"{STUB}/stats", STUB_PORT), (f"{AGENT}/healthz", AGENT_PORT)):
        if get_json(url) is not None:
            raise SystemExit(f"Something else is on port {port}. Run `assistant down` where you started it.")
    running["stub"] = spawn("stub", "stub")
    running["agent"] = spawn("agent", "agent", "--upstream", args.upstream)
    PIDS.write_text(json.dumps(running))
    if wait_for(f"{STUB}/stats") is None or wait_for(f"{AGENT}/healthz") is None:
        raise SystemExit(f"The agent or the stand-in model did not start. See the logs in {RUN}.")
    problem = gateway.start()
    if problem:
        raise SystemExit(f"The gateway did not start: {problem}")
    print(f"Stand-in model on {STUB}.")
    print(f"Agent on {AGENT}, answering with {model}.")
    print(f"Gateway ready on {GATEWAY} ({ENVOY_IMAGE.split('@')[0]}).")
    return 0


def cmd_down(_args) -> int:
    running = pids()
    for name in ("agent", "stub"):
        stop_pid(running.get(name))
    PIDS.unlink(missing_ok=True)
    gateway.stop()
    print("Stopped the agent, the stand-in model and the gateway.")
    return 0


def cmd_token(args) -> int:
    tenant_id, user_id = person(args.person)
    print(issue(tenant_id, user_id))
    return 0


def show_whole(tenant_id: str, user_id: str, question: str) -> int:
    status, body, elapsed = whole_answer(tenant_id, user_id, question)
    print(f"Asked the agent directly, with no token. Status {status}.")
    for tool in body.get("tools", []):
        print(f"  tool: {tool['name']} {json.dumps(tool['arguments'])}")
    print(f"  answer: {body.get('answer') or body.get('error')}")
    print(f"First visible text after {elapsed / 1000:.2f} s, when the whole answer arrived at once.")
    return 0 if status == 200 else 1


def cmd_ask(args) -> int:
    tenant_id, user_id = person(args.person)
    info = tenant(tenant_id)
    if args.whole:
        return show_whole(tenant_id, user_id, args.question)
    token = None if args.no_token else issue(tenant_id, user_id, seconds=-120 if args.expired else 300)
    who = f"{info['users'][user_id]['name']} ({info['name']})"
    print(f"Asked as {who}, through the gateway" + (", with no token." if args.no_token else
                                                      ", with a token that expired two minutes ago." if args.expired else "."))
    reply = stream_answer(args.question, token, hang_up_after=args.hang_up_after)
    if reply.status != 200:
        row = logged(reply.request_id) if reply.request_id else None
        reached = "unknown" if row is None else ("yes" if row.get("upstream") else "no")
        print(f"{reply.status} from the gateway: {reply.error.strip()}")
        if reply.retry_after:
            print(f"Retry-After: {reply.retry_after} s ({reply.limit_scope} limit).")
        print(f"Reached the agent: {reached}.")
        return 0
    for tool in reply.tools:
        print(f"  tool: {tool['name']} {json.dumps(tool['arguments'])}")
    print(f"  answer: {reply.text.strip()}" + (" ..." if reply.hung_up else ""))
    if reply.error:
        print(f"  error event: {reply.error}")
    print(f"First token after {reply.first_text_ms / 1000:.2f} s. "
          + (f"Hung up after {reply.tokens_read} tokens, at {reply.total_ms / 1000:.2f} s." if reply.hung_up
             else f"Answer finished after {reply.total_ms / 1000:.2f} s."))
    row = logged(reply.request_id)
    if row:
        print(f"Gateway log: status {row['status']}, first byte {row['first_byte_ms']} ms, "
              f"whole stream {row['duration_ms']} ms, request {reply.request_id[:8]}.")
    if reply.finish:
        print(f"Cost: ${reply.finish['cost']:.6f}, {reply.finish['completion_tokens']} tokens out.")
    if reply.hung_up:
        show_cancel(reply.request_id)
    return 0


def show_cancel(request_id: str) -> None:
    time.sleep(1.0)
    record = ledger_row(request_id)
    if record is None:
        print("The agent has no record of this request yet.")
        return
    print(f"Agent: {record['status']} after {record['chunks']} tokens streamed; the model call was closed.")
    if record["model"] == stub_settings()[2]:
        stats = get_json(f"{STUB}/stats") or {}
        stopped_at = stats.get("cut_off_words") or []
        print(f"Stand-in model: stopped writing after {stopped_at[-1] if stopped_at else '?'} of 400 words.")
        return
    row = logged(request_id)
    flags = row.get("flags") if row else "?"
    print(f"Gateway log flags: {flags} (DC means the caller hung up), and the agent closed its call to the provider.")


def summarise_client(records: list[dict]) -> dict:
    found = {}
    for kind in ("person", "bot"):
        mine = [record for record in records if record["kind"] == kind]
        answered = [record["first_text_ms"] for record in mine if record["status"] == 200 and not record["error"]]
        admitted = [record for record in mine if record["status"] not in (401, 429)]
        errors = sum(record["error"] for record in admitted)
        found[kind] = {"requests": len(mine), "answered": len(answered), "refused": sum(record["status"] == 429 for record in mine),
                       "errors": errors, "error_rate": round(errors / len(admitted), 4) if admitted else 0.0,
                       "p95_first_text_ms": gatewaylog.percentile(answered, 0.95)}
    return found


def cmd_load(args) -> int:
    health = get_json(f"{AGENT}/healthz")
    if health is None or health.get("model") != stub_settings()[2]:
        raise SystemExit("The load test runs on the stand-in model, so it costs nothing. Run `assistant up --upstream stub` first.")
    LOAD.mkdir(parents=True, exist_ok=True)
    results = LOAD / f"{args.target}-client.jsonl"
    host = AGENT if args.target == "direct" else GATEWAY
    environment = {**os.environ, "MOSSWOOD_TARGET": args.target, "MOSSWOOD_RESULTS": str(results)}
    locust = [sys.executable, "-m", "locust", "-f", str(Path(__file__).with_name("locustfile.py")), "--headless",
              "-u", "28", "-r", "28", "-t", f"{args.seconds}s", "--host", host, "--only-summary", "--stop-timeout", "5"]
    # The naive endpoint keeps writing answers nobody is waiting for. Let those finish before the next run.
    deadline = time.monotonic() + 30
    while (get_json(f"{STUB}/stats") or {}).get("open_streams", 0) > 0 and time.monotonic() < deadline:
        time.sleep(0.5)
    print(f"Load test: 8 people and 20 copies of pf-sync-bot for {args.seconds} s, against the {args.target} target.")
    started = time.time()
    subprocess.run(locust, env=environment, capture_output=True, text=True)
    ended = time.time()
    time.sleep(1.0)  # one more flush of the gateway's access log
    records = [json.loads(line) for line in results.read_text().splitlines()] if results.exists() else []
    summary = {"target": args.target, "started": started, "ended": ended, "client": summarise_client(records)}
    if args.target == "gateway":
        summary["gateway"] = gatewaylog.summarise(gatewaylog.rows(since=started, until=ended + 5), gateway.limits())
    (LOAD / f"{args.target}.json").write_text(json.dumps(summary, indent=2))
    print_load(summary)
    return 0


def ms(value) -> str:
    return "none" if value is None else f"{value:.0f} ms"


def print_load(summary: dict) -> None:
    people, bot = summary["client"]["person"], summary["client"]["bot"]
    print(f"People, as their laptops saw it: {people['requests']} asked, {people['answered']} answered, "
          f"p95 first text {ms(people['p95_first_text_ms'])}, error rate {people['error_rate']:.1%}.")
    print(f"pf-sync-bot: {bot['requests']} asked, {bot['answered']} answered, {bot['refused']} refused with 429.")
    if "gateway" not in summary:
        return
    numbers = summary["gateway"]
    overall = numbers["overall"]
    print(f"Gateway log: {overall['requests']} requests, p95 time to first token {ms(overall['p95_first_token_ms'])}, "
          f"error rate {overall['error_rate']:.1%}, {overall['refused_user'] + overall['refused_tenant']} refused with 429.")
    for tenant_id, row in numbers["tenants"].items():
        print(f"  {tenant(tenant_id)['name']}: {row['answered']} answered, p95 first token {ms(row['p95_first_token_ms'])}, "
              f"{row['refused_user']} refused at a user limit, {row['refused_tenant']} at the workspace limit, {row['errors']} errors.")
    for case in numbers["needs_operator"]:
        print(f"Needs the operator: {case['name']}. {case['reason']}")


def cmd_report(_args) -> int:
    for target in ("direct", "gateway"):
        path = LOAD / f"{target}.json"
        if path.exists():
            print(f"[{target}]")
            print_load(json.loads(path.read_text()))
    return 0


def cmd_limit(args) -> int:
    if args.tenant not in gateway.limits():
        raise SystemExit(f"No tenant {args.tenant!r}.")
    entry = gateway.decide(args.tenant, keep=args.keep, new_limit=args.raise_to, operator="operator (CLI)", reason=args.reason)
    problem = gateway.reload() if gateway.running() else ""
    print(f"{tenant(args.tenant)['name']}: {entry['decision']}, limit now {gateway.limits()[args.tenant]} per window."
          + (f" Reload failed: {problem}" if problem else ""))
    return 0


def cmd_serve(args) -> int:
    from mosswood.serve import main as serve_main
    return serve_main(args.port)


def main() -> int:
    parser = argparse.ArgumentParser(prog="assistant", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    up = commands.add_parser("up", help="start the stand-in model, the agent and the Envoy gateway")
    up.add_argument("--upstream", choices=["live", "stub"], default="live")
    commands.add_parser("down", help="stop everything `up` started")
    agent = commands.add_parser("agent", help="run the agent in the foreground")
    agent.add_argument("--upstream", choices=["live", "stub"], default="live")
    commands.add_parser("stub", help="run the stand-in model in the foreground")
    token = commands.add_parser("token", help="print a token the web app would issue")
    token.add_argument("person", nargs="?", default=DEFAULT_PERSON)
    ask = commands.add_parser("ask", help="ask a question through the gateway")
    ask.add_argument("question")
    ask.add_argument("--as", dest="person", default=DEFAULT_PERSON)
    ask.add_argument("--whole", action="store_true", help="the naive call: straight to the agent, one JSON body")
    ask.add_argument("--no-token", action="store_true")
    ask.add_argument("--expired", action="store_true")
    ask.add_argument("--hang-up-after", type=int)
    load = commands.add_parser("load", help="run the Locust load test")
    load.add_argument("--target", choices=["direct", "gateway"], default="gateway")
    load.add_argument("--seconds", type=int, default=20)
    commands.add_parser("report", help="print the last load test, read from the gateway's log")
    limit = commands.add_parser("limit", help="decide on a tenant at its workspace limit")
    limit.add_argument("tenant")
    choice = limit.add_mutually_exclusive_group(required=True)
    choice.add_argument("--raise-to", type=int)
    choice.add_argument("--keep", action="store_true")
    limit.add_argument("--reason", default="")
    serve = commands.add_parser("serve", help="the Mosswood app and the gateway operator view")
    serve.add_argument("--port", type=int, default=PAGE_PORT)
    args = parser.parse_args()
    handlers = {"up": cmd_up, "down": cmd_down, "agent": cmd_agent, "stub": cmd_stub, "token": cmd_token, "ask": cmd_ask,
                "load": cmd_load, "report": cmd_report, "limit": cmd_limit, "serve": cmd_serve}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
