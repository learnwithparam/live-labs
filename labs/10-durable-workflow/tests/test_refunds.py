"""Keyless tests: a fake model and Temporal's time-skipping test server, so three days pass in a second."""

import json
import threading
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from refunds import naive
from refunds.decide import APPROVAL_OVER, decide
from refunds.ledger import Ledger
from refunds.requests import request, requests
from refunds.review import Crash, raise_crash, score
from refunds.workflow import RefundWorkflow, Steps, worker_parts, workflow_id


class FakeLLM:
    """Answers every refund with finance's own ruling, or a fixed amount, and counts the calls."""

    def __init__(self, amount: float | None = None):
        self.amount, self.calls = amount, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls += 1
        refund = next(r for r in requests() if r.message in kwargs["messages"][1]["content"])
        amount = refund.ruling if self.amount is None else self.amount
        reply = json.dumps({"amount": amount, "rules": ["R1"], "reason": "Per the policy."})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))], usage=SimpleNamespace(cost=0.0001))


def test_eight_requests_with_a_ruling_and_code_decides_who_approves():
    reqs = requests()
    assert [r.id for r in reqs] == [f"RF-30{i}" for i in range(1, 9)]
    assert sum(r.ruling > APPROVAL_OVER for r in reqs) == 3
    got, cost = decide(request("RF-301"), FakeLLM(), "m")
    assert got == {"amount": 1241.0, "rules": ["R1"], "reason": "Per the policy.", "needs_approval": True} and cost == 0.0001


def test_the_amount_is_capped_at_what_the_customer_paid():
    got, _ = decide(request("RF-302"), FakeLLM(amount=99999), "m")
    assert got["amount"] == 218.0  # the side table's price plus delivery


def test_the_ledger_pays_once_per_idempotency_key(tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    first = ledger.pay("RF-1", "Ana", 100, "durable", "k")
    again = ledger.pay("RF-1", "Ana", 100, "durable", "k")
    assert again["id"] == first["id"] and again["replayed"] and len(ledger.payouts()) == 1
    # Without a key, every call is a new payout.
    ledger.pay("RF-1", "Ana", 100, "naive", None)
    ledger.pay("RF-1", "Ana", 100, "naive", None)
    assert len(ledger.payouts("naive")) == 2


def test_the_plain_script_rerun_after_a_crash_pays_twice_and_asks_the_model_again(tmp_path):
    ledger, llm = Ledger(tmp_path / "l.db"), FakeLLM()
    ledger.record_verdict("RF-301", "approved", "Priya")
    with pytest.raises(Crash):
        naive.run("RF-301", ledger, llm, "m", crash_after_pay=True, crash=raise_crash, poll=0)
    naive.run("RF-301", ledger, llm, "m", poll=0)
    assert len(ledger.payouts("naive")) == 2 and llm.calls == 2
    got = score([request("RF-301")], ledger, "naive", {"RF-301": 1241.0})
    assert got["paid_twice"] == 1 and got["model_calls"] == 2


@pytest.fixture()
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


async def run_refund(env, ledger: Ledger, llm, refund: str, while_running=None, crash_after_pay=False, crash=None):
    steps = Steps(ledger, llm, "m", crash_after_pay=crash_after_pay, **({"crash": crash} if crash else {}))
    async with Worker(env.client, activity_executor=ThreadPoolExecutor(2), **worker_parts(steps)):
        handle = await env.client.start_workflow(RefundWorkflow.run, refund, id=workflow_id(refund, uuid.uuid4().hex[:4]), task_queue="refunds")
        if while_running:
            await while_running(handle)
        return await handle.result()


async def test_a_small_refund_pays_without_a_manager(env, tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    got = await run_refund(env, ledger, FakeLLM(), "RF-302")
    assert got["stage"] == "paid" and got["verdict"] is None
    assert [p["cents"] for p in ledger.payouts("durable")] == [21800]


async def test_a_large_refund_waits_for_the_manager_then_pays_once(env, tmp_path):
    ledger = Ledger(tmp_path / "l.db")

    async def approve(handle):
        # Before the signal, it waits; nothing is paid.
        await handle.query(RefundWorkflow.status)
        assert ledger.payouts() == []
        await handle.signal(RefundWorkflow.approve, "Priya")

    got = await run_refund(env, ledger, FakeLLM(), "RF-301", approve)
    assert got["stage"] == "paid" and got["manager"] == "Priya"
    assert len(ledger.payouts("durable")) == 1


async def test_a_rejected_refund_pays_nothing(env, tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    got = await run_refund(env, ledger, FakeLLM(), "RF-301", lambda h: h.signal(RefundWorkflow.reject, "Priya"))
    assert got["stage"] == "rejected" and ledger.payouts() == []


async def test_after_three_days_without_an_answer_the_refund_is_escalated_and_still_waits(env, tmp_path):
    ledger = Ledger(tmp_path / "l.db")

    async def wait_out(handle):
        await env.sleep(timedelta(days=3, hours=1))
        assert (await handle.query(RefundWorkflow.status))["stage"] == "escalated"
        await handle.signal(RefundWorkflow.approve, "Regional manager")

    got = await run_refund(env, ledger, FakeLLM(), "RF-306", wait_out)
    assert got["stage"] == "paid" and len(ledger.payouts()) == 1


async def test_a_crash_after_paying_retries_the_payment_and_the_key_stops_a_second_payout(env, tmp_path):
    ledger, llm = Ledger(tmp_path / "l.db"), FakeLLM()

    def crash():
        # In the worker this is the process dying. Here the activity fails right after the payout.
        raise RuntimeError("worker died")

    got = await run_refund(env, ledger, llm, "RF-302", crash_after_pay=True, crash=crash)
    assert got["stage"] == "paid" and got["payout"]["replayed"] is True
    assert len(ledger.payouts("durable")) == 1
    # The decision is in the history, so the retry did not ask the model again.
    assert llm.calls == 1


async def test_a_zero_refund_is_declined_without_paying(env, tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    got = await run_refund(env, ledger, FakeLLM(), "RF-303")
    assert got["stage"] == "declined" and ledger.payouts() == []


async def test_the_desk_reads_a_refund_from_its_history_and_refuses_a_foreign_origin(env, tmp_path):
    import asyncio

    from refunds.serve import Room, serve, status

    ledger = Ledger(tmp_path / "l.db")
    steps = Steps(ledger, FakeLLM(), "m")
    async with Worker(env.client, activity_executor=ThreadPoolExecutor(2), **worker_parts(steps)):
        room = Room(env.client, "unused", asyncio.get_running_loop(), ledger=ledger.path, review_file=tmp_path / "r.json")
        server = serve(room, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"

        def call(path, data=None, headers=None):
            body = json.dumps(data).encode() if data is not None else None
            req = urllib.request.Request(base + path, body, headers={"Content-Type": "application/json", **(headers or {})})
            with urllib.request.urlopen(req) as res:
                return json.loads(res.read())

        await asyncio.to_thread(call, "/api/start", {"refund": "RF-301"})
        handle = env.client.get_workflow_handle(room.started["RF-301"])
        # The desk reads the history, which shows "waiting" once the decide activity has finished.
        for _ in range(100):
            desk = await asyncio.to_thread(call, "/api/desk")
            if desk["flows"]["RF-301"]["status"]["stage"] == "waiting":
                break
            await asyncio.sleep(0.05)
        assert len(desk["refunds"]) == 8 and desk["flows"]["RF-301"]["status"]["stage"] == "waiting"
        await asyncio.to_thread(call, "/api/verdict", {"refund": "RF-301"})
        await handle.result()
        got = status(await handle.fetch_history())
        assert got["stage"] == "paid" and got["verdict"] == "approved" and got["done"]
        with pytest.raises(urllib.error.HTTPError) as refused:
            await asyncio.to_thread(call, "/api/desk", None, {"Origin": "http://evil.example"})
        assert refused.value.code == 403
        server.shutdown()
