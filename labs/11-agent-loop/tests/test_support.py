"""Keyless tests: the policy lives in the tools, both engines resolve the same chats from the same script,
and both stop on the same budgets."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from fakes import FakeADK, FakeOpenAI, call
from kestrel.budget import Budget
from kestrel.chats import chat, chats
from kestrel.loop import tool_schema
from kestrel.review import outcome, run_chat, score
from kestrel.shop import Shop
from kestrel.tools import OrderTools

MODEL = "google/gemini-2.5-flash"


def models(script, tokens=(1000, 50)):
    return {"client": FakeOpenAI(list(script), tokens), "model": MODEL, "adk": FakeADK(model="fake", script=list(script), tokens=tokens)}


def test_ten_chats_eight_to_resolve_two_for_a_person():
    got = chats()
    assert len(got) == 10 and sum(c.handoff for c in got) == 2 and sum(bool(c.expect) for c in got) == 5


def test_the_tools_hold_the_policy_whatever_the_model_asks():
    shop = Shop()
    maya, ben, ava, jon = (OrderTools(shop, c) for c in ("C-101", "C-108", "C-107", "C-106"))
    assert "error" in ben.cancel_order("KO-1041")  # Maya's order: looks like no order at all
    assert ben.get_order("KO-1041") == {"error": "No order KO-1041 on this customer's account."}
    assert "cannot be cancelled" in ben.cancel_order("KO-1045")["error"]  # shipped
    assert "person must approve" in ava.refund_damaged_item("KO-1036", "poles snapped")["error"]  # $449
    assert "returns close" in jon.start_return("KO-1020")["error"]  # 45 days
    assert shop.changes() == []
    # The over-$300 refund is with a person even if the model never calls hand_off.
    assert shop.handoffs() == ["Damaged Ultralight 1 person tent, $449.00 refund is over $300 (P5)"]
    maya.cancel_order("KO-1041")
    assert shop.changes() == [{"kind": "cancel", "order_id": "KO-1041", "amount": 289.0, "detail": ""}]


def test_a_tool_schema_comes_from_type_hints_and_the_docstring():
    got = tool_schema(OrderTools(Shop(), "C-101").change_address)["function"]
    assert got["name"] == "change_address" and got["description"].startswith("Change the delivery address")
    assert got["parameters"]["required"] == ["order_id", "new_address"]
    assert got["parameters"]["properties"]["new_address"] == {"type": "string", "description": "The full new delivery address, with postcode."}


SCRIPT_KC10 = [[call("find_orders")], [call("cancel_order", order_id="KO-1041"), call("start_return", order_id="KO-1038")],
               "Cancelled the tent and started the shoe return."]


@pytest.mark.parametrize("engine", ["loop", "adk"])
def test_both_engines_resolve_the_two_part_chat_from_the_same_script(engine):
    c = chat("KC-10")
    steps = []
    shop, budget, got = run_chat(engine, c.customer, c.message, models(SCRIPT_KC10), steps.append)
    assert outcome(c, shop, got) == "resolved" and budget.steps == 3
    assert [s["tool"] for s in steps if s["kind"] == "call"] == ["find_orders", "cancel_order", "start_return"]


@pytest.mark.parametrize("engine", ["loop", "adk"])
def test_a_model_that_never_stops_is_handed_to_a_person_at_the_step_budget(engine):
    c = chat("KC-07")
    shop, budget, got = run_chat(engine, c.customer, c.message, models([]))
    assert got["stopped"] == "budget" and budget.steps == 8
    assert shop.handoffs() == ["step budget: 8 model calls"] and outcome(c, shop, got) == "over budget"


@pytest.mark.parametrize("engine", ["loop", "adk"])
def test_a_long_context_is_handed_to_a_person_at_the_cost_budget(engine):
    c = chat("KC-07")
    # 20,000 tokens in per call at $0.30 a million: two calls take it past one cent.
    shop, budget, got = run_chat(engine, c.customer, c.message, models([], tokens=(20000, 100)))
    assert got["stopped"] == "budget" and budget.steps == 2 and budget.cost >= 0.01
    assert shop.handoffs()[0].startswith("cost budget")


def test_the_reply_only_bot_promises_but_the_order_system_does_not_move():
    c = chat("KC-01")
    shop, _, got = run_chat("reply", c.customer, c.message, models(["Done, I have cancelled KO-1046 for you."]))
    assert "cancelled" in got["reply"] and shop.changes() == [] and outcome(c, shop, got) == "wrong state"


def test_handing_off_a_chat_that_needs_a_person_is_right_and_acting_on_it_is_not():
    c = chat("KC-06")
    shop, _, got = run_chat("loop", c.customer, c.message, models([[call("hand_off", reason="$449 refund")], "A colleague will reply."]))
    assert outcome(c, shop, got) == "handed off"
    rows = [{"handoff": True, "expect": [], "outcome": "handed off", "steps": 2, "cost": 0.001},
            {"handoff": False, "expect": [{"kind": "cancel"}], "outcome": "resolved", "steps": 3, "cost": 0.002},
            {"handoff": False, "expect": [], "outcome": "handed off needlessly", "steps": 2, "cost": 0.001}]
    got = score(rows)
    assert (got["resolved"], got["resolvable"], got["actions_done"], got["handed_off_right"], got["needless_hand_offs"]) == (1, 2, 1, 1, 1)


def test_the_help_page_runs_a_chat_and_refuses_a_foreign_origin():
    from kestrel.serve import Desk, serve

    desk = Desk(lambda: models([[call("cancel_order", order_id="KO-1046")], "Cancelled."]))
    server = serve(desk, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    def get(path, data=None, headers=None):
        req = urllib.request.Request(base + path, json.dumps(data).encode() if data else None,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read())

    run = get("/api/run", {"chat": "KC-01", "engine": "loop"})
    for _ in range(200):
        state = get(f"/api/run/{run['id']}")
        if state["done"]:
            break
    assert state["outcome"] == "resolved"
    assert next(o for o in state["orders"] if o["id"] == "KO-1046")["status"] == "cancelled"
    with pytest.raises(urllib.error.HTTPError) as refused:
        get("/api/chats", None, {"Origin": "http://evil.example"})
    assert refused.value.code == 403
    server.shutdown()
