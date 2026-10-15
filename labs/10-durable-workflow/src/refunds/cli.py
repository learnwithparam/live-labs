"""`refund naive`, `refund run`, `refund ledger`, `refund review`, `refund server`, `refund worker` and `refund serve`."""

import argparse
import asyncio
import contextlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from refunds.ledger import DEFAULT, Ledger
from refunds.workflow import TASK_QUEUE

REVIEW_LEDGER = Path("out/review-ledger.db")
TEMPORAL_DB = Path("out/temporal.db")


def client_and_model():
    from openai import OpenAI

    from refunds.config import llm_settings

    base_url, key, model = llm_settings()
    return OpenAI(base_url=base_url, api_key=key, timeout=120, max_retries=4), model


async def connect(address: str):
    from temporalio.client import Client

    return await Client.connect(address)


@contextlib.asynccontextmanager
async def temporal(keep: Path | None = None):
    """The Temporal server at TEMPORAL_ADDRESS, or a dev server started here when nothing answers there.

    The dev server is one binary the SDK downloads on first use. `keep` names a file for its history,
    so workflows outlive a restart of the server too.
    """
    from temporalio.testing import WorkflowEnvironment

    from refunds.config import temporal_address

    address = temporal_address()
    try:
        yield await connect(address), address
        return
    except RuntimeError:
        pass
    host, port = address.rsplit(":", 1)
    extra = {"dev_server_database_filename": str(keep)} if keep else {}
    async with await WorkflowEnvironment.start_local(ip=host, port=int(port), ui=True, ui_port=int(port) + 1000, **extra) as env:
        print(f"Started a Temporal dev server on {address}, with its web UI on http://{host}:{int(port) + 1000}")
        yield env.client, address


def start_worker(address: str, ledger: Path, crash_after_pay: bool, queue: str = TASK_QUEUE) -> subprocess.Popen:
    args = [sys.executable, "-c", "from refunds.cli import main; main()", "worker", "--address", address, "--ledger", str(ledger),
            "--queue", queue]
    return subprocess.Popen(args + (["--crash-after-pay"] if crash_after_pay else []))


def worker(address: str, ledger: Path, crash_after_pay: bool, queue: str = TASK_QUEUE) -> int:
    from temporalio.worker import Worker

    from refunds.workflow import Steps, worker_parts

    client, model = client_and_model()
    steps = Steps(Ledger(ledger), client, model, crash_after_pay=crash_after_pay)

    async def go():
        # Activities here are plain functions, so the worker runs them on threads.
        async with Worker(await connect(address), activity_executor=ThreadPoolExecutor(4), **worker_parts(steps, queue)):
            await asyncio.Event().wait()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(go())
    return 0


def naive_run(refund: str, crash_after_pay: bool) -> int:
    from refunds import naive

    client, model = client_and_model()
    ledger = Ledger()
    if crash_after_pay:
        print("This run will crash right after the payout, before it can record anything.")
    got = naive.run(refund, ledger, client, model, crash_after_pay=crash_after_pay)
    print(f"{refund}: {got['stage']}, ${got['decision']['amount']:.2f} ({', '.join(got['decision']['rules'])})")
    return 0


def durable_run(refund: str, crash_after_pay: bool) -> int:
    """Start one refund's workflow and keep a worker alive until it finishes, printing each stage."""
    from refunds.history import status
    from refunds.workflow import RefundWorkflow, workflow_id
    from temporalio.client import WorkflowExecutionStatus

    async def go():
        async with temporal(TEMPORAL_DB) as (client, address):
            proc = start_worker(address, DEFAULT, crash_after_pay)
            handle = await client.start_workflow(RefundWorkflow.run, refund, id=workflow_id(refund), task_queue=TASK_QUEUE)
            print(f"Started workflow {handle.id}")
            seen = None
            try:
                while (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
                    if proc.poll() is not None:
                        print("The worker died. Starting a new one: it replays the history and carries on.")
                        proc = start_worker(address, DEFAULT, False)
                    # The history, not a query: a query waits for a worker, and this one may be dead.
                    stage = status(await handle.fetch_history())["stage"]
                    if stage != seen:
                        print(f"  {stage}")
                        if stage == "waiting":
                            print(f"  Over $500: a manager must approve. In another terminal: refund approve {refund}")
                        seen = stage
                    await asyncio.sleep(0.3)
            finally:
                proc.terminate()
            got = await handle.result()
            print(f"{refund}: {got['stage']}, ${got['decision']['amount']:.2f} ({', '.join(got['decision']['rules'])})")
            if (got.get("payout") or {}).get("replayed"):
                print(f"The provider had already paid key {got['payout']['idempotency_key']}, so it sent nothing new.")

    asyncio.run(go())
    return 0


def approve(refund: str, reject: bool, manager: str) -> int:
    from refunds.config import temporal_address
    from refunds.workflow import RefundWorkflow, workflow_id

    async def go():
        handle = (await connect(temporal_address())).get_workflow_handle(workflow_id(refund))
        await handle.signal(RefundWorkflow.reject if reject else RefundWorkflow.approve, manager)
        print(f"{'Rejected' if reject else 'Approved'} {refund} as {manager}.")

    asyncio.run(go())
    return 0


def show_ledger(path: Path) -> int:
    rows = Ledger(path).payouts()
    print(f"{'payout':<8}{'refund':<9}{'customer':<14}{'amount':>10}  {'how':<9}idempotency key")
    for p in rows:
        print(f"{p['id']:<8}{p['refund']:<9}{p['customer']:<14}{p['cents'] / 100:>10.2f}  {p['how']:<9}{p['idempotency_key'] or 'none'}")
    return 0


def review() -> int:
    from refunds.requests import requests
    from refunds.review import WAYS, durable_way, naive_way, score

    client, model = client_and_model()
    reqs = requests()
    ledger = Ledger(REVIEW_LEDGER)
    ledger.clear()
    for marker in (REVIEW_LEDGER.parent / "crashed").glob("*"):
        marker.unlink()
    print(f"{len(reqs)} refunds each way, each crashed right after its payout. Model {model}.\n")
    show = lambda e: print(f"  {e['how']:<8}{e.get('refund', ''):<8}{e['event']}")
    decided = {"naive": naive_way(reqs, ledger, client, model, on_event=show)}

    async def go():
        async with temporal() as (temporal_client, address):
            import uuid

            run = uuid.uuid4().hex[:6]
            # Its own queue: a desk or `refund run` worker on the same server must not take these refunds.
            queue = f"refunds-review-{run}"
            return await durable_way(temporal_client, reqs, lambda: start_worker(address, REVIEW_LEDGER, True, queue),
                                     run, queue, on_event=show)

    decided["durable"], deaths, attempts = asyncio.run(go())
    result = {how: score(reqs, ledger, how, decided[how]) for how in WAYS} | {"worker_deaths": deaths, "decide_attempts": attempts}
    print(f"\n{'':<10}{'paid twice':<12}{'lost':<7}{'paid right':<13}{'model calls':<13}dollars out (due)")
    for how in WAYS:
        s = result[how]
        print(f"{how:<10}{s['paid_twice']:<12}{s['lost']:<7}{str(s['paid_right']) + ' of ' + str(s['due']):<13}"
              f"{s['model_calls']:<13}${s['dollars_out']:,.2f} (${s['dollars_due']:,.2f})")
    retried = attempts - len(reqs)
    lost = result["durable"]["lost"]
    print(f"\nThe durable worker died {deaths} times and lost {lost or 'nothing'}. {retried} model calls were a decide that was")
    print("still running when the worker died; a finished decide was never asked again. Every payout: out/review-ledger.db")
    result["payouts"] = ledger.payouts()
    Path("out/review.json").write_text(json.dumps(result, indent=1))
    return 0


def server() -> int:
    async def go():
        async with temporal(TEMPORAL_DB):
            print("Ctrl-C stops it. Workflow history is kept in out/temporal.db.")
            await asyncio.Event().wait()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(go())
    return 0


def serve_page(port: int) -> int:
    from refunds.serve import Room, serve

    async def go():
        async with temporal(TEMPORAL_DB) as (client, address):
            room = Room(client, address, asyncio.get_running_loop(), llm=client_and_model)
            page = serve(room, port)
            print(f"Fernhill refunds desk on http://127.0.0.1:{port}. Ctrl-C stops it.")
            try:
                await asyncio.to_thread(page.serve_forever)
            finally:
                room.stop_worker()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(go())
    return 0


def main() -> None:
    from refunds.config import temporal_address

    parser = argparse.ArgumentParser(prog="refund")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("naive", "run"):
        one = sub.add_parser(name)
        one.add_argument("refund")
        one.add_argument("--crash-after-pay", action="store_true", help="die right after the payout, once")
    ok = sub.add_parser("approve")
    ok.add_argument("refund")
    ok.add_argument("--reject", action="store_true")
    ok.add_argument("--manager", default="Priya Shah, store manager")
    sub.add_parser("ledger").add_argument("--review", action="store_true", help="the review's ledger")
    sub.add_parser("review")
    sub.add_parser("server")
    work = sub.add_parser("worker")
    work.add_argument("--address", default=None)
    work.add_argument("--ledger", type=Path, default=DEFAULT)
    work.add_argument("--crash-after-pay", action="store_true")
    work.add_argument("--queue", default=TASK_QUEUE)
    sub.add_parser("serve").add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "naive":
        sys.exit(naive_run(args.refund, args.crash_after_pay))
    if args.command == "run":
        sys.exit(durable_run(args.refund, args.crash_after_pay))
    if args.command == "approve":
        sys.exit(approve(args.refund, args.reject, args.manager))
    if args.command == "ledger":
        sys.exit(show_ledger(REVIEW_LEDGER if args.review else DEFAULT))
    if args.command == "review":
        sys.exit(review())
    if args.command == "server":
        sys.exit(server())
    if args.command == "worker":
        sys.exit(worker(args.address or temporal_address(), args.ledger, args.crash_after_pay, args.queue))
    sys.exit(serve_page(args.port))
