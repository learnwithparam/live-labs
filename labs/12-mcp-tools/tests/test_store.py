"""Keyless tests: both MCP servers change the same store, the designed tools refuse to guess, the model
reads every error the designed tools raise, and the judge scores the store, not the reply."""

import asyncio
import json
import threading
import urllib.error
import urllib.request

import pytest
from mcp.client import Client

from fakes import FakeOpenAI, call
from tidepool import api_server, tools_server
from tidepool.assistant import ask
from tidepool.review import judge, run_task, score, task, tasks
from tidepool.serve import Admin, serve
from tidepool.store import Store

MODEL = "google/gemini-2.5-flash"


def call_tool(build, store: Store, tool: str, **args):
    async def run():
        async with Client(build(store)) as mcp:
            return await mcp.call_tool(tool, args)

    result = asyncio.run(run())
    return result.is_error, "\n".join(block.text for block in result.content)


def test_twenty_tasks_two_ambiguous_two_questions():
    every = tasks()
    assert [each["id"] for each in every] == [f"TP-{number:02d}" for number in range(1, 21)]
    assert sorted(each["id"] for each in every if each.get("should_ask")) == ["TP-11", "TP-18"]
    assert sorted(each["id"] for each in every if each.get("answer_has")) == ["TP-12", "TP-20"]


def test_a_fresh_store_has_no_changes_and_records_each_one():
    store = Store()
    assert store.changes() == {}
    store.variant("KEN-1K")[1]["price"] = 70
    assert store.changes() == {"variant:KEN-1K.price": 70.0}
    # A second store starts from the file again, so one task never leaks into the next.
    assert Store().changes() == {}


def test_designed_tools_take_the_merchants_words():
    store = Store()
    failed, text = call_tool(tools_server.build, store, "set_price", item="Colombia 1kg", price=48)
    assert not failed and "was $50.00" in text
    assert store.changes() == {"variant:COL-1K.price": 48.0}


def test_an_ambiguous_item_is_refused_with_the_choices_and_changes_nothing():
    store = Store()
    failed, text = call_tool(tools_server.build, store, "set_price", item="kenya", price=19)
    assert failed and "KEN-250" in text and "KEN-1K" in text and "Ask the merchant which one" in text
    assert store.changes() == {}


def test_designed_errors_say_what_to_do_next():
    store = Store()
    _, text = call_tool(tools_server.build, store, "create_discount", code="FALL", percent=10, ends_on="2026-10-01")
    assert "not after today, 2026-10-15" in text
    _, text = call_tool(tools_server.build, store, "set_collections", handle="holiday-blend", collections=["Xmas"])
    assert "The collections are: Single origin" in text
    _, text = call_tool(tools_server.build, store, "find_order", order_number="9999")
    assert "#1041" in text and "#1047" in text


def test_the_api_server_needs_ids_and_hides_why_it_failed():
    store = Store()
    failed, text = call_tool(api_server.build, store, "inventory_level_adjust",
                             inventory_item_id=49102, location_id=1, available_adjustment=5)
    assert failed and store.changes() == {}
    failed, _ = call_tool(api_server.build, store, "inventory_level_adjust",
                          inventory_item_id=49102, location_id=61, available_adjustment=5)
    assert not failed and store.changes() == {"variant:ETH-1K.stock": 17}


def test_annotations_mark_refunds_destructive_and_lookups_read_only():
    async def listed():
        async with Client(tools_server.build(Store())) as mcp:
            return {tool.name: tool.annotations for tool in (await mcp.list_tools()).tools}

    annotations = asyncio.run(listed())
    assert annotations["refund_items"].destructive_hint is True
    assert annotations["find_products"].read_only_hint and annotations["find_order"].read_only_hint
    assert not any(annotation.destructive_hint for name, annotation in annotations.items() if name != "refund_items")


def test_the_model_reads_the_designed_tools_error_text():
    model = FakeOpenAI([[call("set_price", item="kenya", price=19)], "Which Kenya, the 250g or the 1kg?"])
    store = Store()
    result = asyncio.run(ask(tools_server.build(store), "Change the Kenya price to $19.", model, MODEL))
    tool_message = next(message for message in model.seen[-1]["messages"] if message["role"] == "tool")
    assert "Ask the merchant which one" in tool_message["content"]
    assert result["calls"][0]["error"] and store.changes() == {}


def test_a_follow_up_answer_continues_from_the_history():
    model = FakeOpenAI([[call("set_price", item="KEN-1K", price=19)], "Done."])
    history = [{"role": "user", "content": "Change the Kenya price to $19."},
               {"role": "assistant", "content": "Which one?"}]
    store = Store()
    asyncio.run(ask(tools_server.build(store), "The 1kg.", model, MODEL, history=history))
    assert [message["content"] for message in model.seen[0]["messages"][1:]] == [
        "Change the Kenya price to $19.", "Which one?", "The 1kg."]
    assert store.changes() == {"variant:KEN-1K.price": 19.0}


def test_the_judge_reads_the_store_not_the_reply():
    stock_task = task("TP-02")
    assert judge(stock_task, {}, "I added 24 bags.") == (False, "not done: variant:COL-1K.stock")
    assert judge(stock_task, {"variant:COL-1K.stock": 27}, "Done.") == (True, "done")
    assert judge(stock_task, {"variant:COL-1K.stock": 27, "variant:COL-250.stock": 0}, "Done.")[0] is False
    kenya = task("TP-11")
    assert judge(kenya, {}, "Which one, the 250g or the 1kg?") == (True, "asked the merchant")
    assert judge(kenya, {}, "I could not do that.") == (False, "should have asked which one")
    assert judge(kenya, {"variant:KEN-1K.price": 19.0}, "Done?")[0] is False


def test_a_scripted_run_scores_success_wrong_calls_and_cost():
    model = FakeOpenAI([[call("set_price", item="DRP-GLASS", price=38)], [call("set_price", item="DRP-CER", price=38)],
                        "The dripper is now $38."], tokens=(1_000_000, 0))
    row = asyncio.run(run_task("designed", task("TP-01"), model, MODEL))
    assert row["ok"] and row["changes"] == {"variant:DRP-CER.price": 38.0}
    totals = score([row])
    assert totals["succeeded"] == 1 and totals["wrong_calls"] == 1 and totals["calls"] == 2
    # Three model turns of a million prompt tokens each, at the default model's list price.
    assert totals["cost"] == pytest.approx(3 * 0.30)


def test_serve_answers_only_its_own_page(tmp_path):
    admin = Admin(lambda: (FakeOpenAI(["Done."]), MODEL), review_file=tmp_path / "none.json")
    server = serve(admin, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        state = json.loads(urllib.request.urlopen(f"{base}/api/state").read())
        assert len(state["tasks"]) == 20 and len(state["tools"]["designed"]) == 12 and state["review"] is None
        assert urllib.request.urlopen(f"{base}/?assistant").status == 200
        foreign = urllib.request.Request(f"{base}/api/state", headers={"Origin": "http://evil.example"})
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(foreign)
        assert refused.value.code == 403
    finally:
        server.shutdown()
