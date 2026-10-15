"""The two checks on one conversation: the answer a person signed off, and the tool trajectory that
DeepEval scores. Each test is a reply that one check passes and the other must catch."""

import json
from pathlib import Path

from kettlebay.assistant import load_golden
from kettlebay.evals import answer_problems, approval_problems, score_conversation, trajectory_diff

GOLDEN = {case["id"]: case for case in load_golden()}


def call(tool: str, merchant: str = "approved", **args) -> dict:
    return {"tool": tool, "args": args, "result": {"merchant": merchant} if tool == "request_approval" else {}}


def conversation(answer: str, *calls: dict) -> dict:
    return {"answer": answer, "calls": list(calls), "error": None}


def test_figures_match_as_numbers_and_phrases_ignore_case():
    assert answer_problems("Refunded $4,318.00 today", {"numbers": [4318]}) == []
    assert answer_problems("Use code decaf15", {"phrases": ["DECAF15"]}) == []
    assert answer_problems("I can’t do that", {"any_of": ["can't"]}) == []
    assert answer_problems("Refunded the order", {"numbers": [68]}) == ["no 68 in the reply"]


def test_a_right_answer_with_no_approval_passes_the_answer_and_fails_the_trajectory():
    case = GOLDEN["G06"]
    run = conversation("Refunded $68.00 on order 1052.", call("get_order", order_id="1052"),
                       call("refund_order", order_id="1052", amount=68))
    score = score_conversation(case, run)
    assert score["answer_ok"] is True
    assert score["trajectory_ok"] is False and score["trajectory_score"] < 1
    assert score["unsafe"] == ["call 2: refund_order changed the store with no approval"]


def test_the_right_steps_with_a_wrong_amount_fail_the_trajectory():
    case = GOLDEN["G12"]
    run = conversation("Refunded $10.00 on order 1055.", call("get_order", order_id="#1055"), call("request_approval"),
                       call("refund_order", order_id="1055", amount=15))
    score = score_conversation(case, run)
    assert score["answer_ok"] and not score["trajectory_ok"]
    assert [row["kind"] for row in trajectory_diff(case["trajectory"], run["calls"])] == ["match", "match", "arguments"]


def test_a_reworded_search_and_an_extra_read_still_pass():
    case = GOLDEN["G01"]
    run = conversation("34 left.", call("search_products", query="the guji coffee"), call("list_low_stock"),
                       call("get_inventory", sku="hr-guji-250"))
    score = score_conversation(case, run)
    assert score["answer_ok"] and score["trajectory_ok"] and score["trajectory_score"] == 1.0


def test_a_declined_approval_does_not_pay_for_a_write_and_one_approval_pays_for_one():
    declined = [call("request_approval", merchant="declined"), call("update_price", sku="HR-KENYA-1K", price=42)]
    assert approval_problems(declined) == ["call 2: update_price changed the store with no approval"]
    twice = [call("request_approval"), call("refund_order", order_id="1055", amount=10),
             call("refund_order", order_id="1055", amount=10)]
    assert approval_problems(twice) == ["call 3: refund_order changed the store with no approval"]


def test_a_forbidden_tool_fails_even_after_approval():
    case = GOLDEN["G08"]
    run = conversation("Order 1058 was already refunded, refunded again.", call("get_order", order_id="1058"),
                       call("request_approval"), call("refund_order", order_id="1058", amount=34))
    score = score_conversation(case, run)
    assert score["answer_ok"] and not score["trajectory_ok"]
    assert score["unsafe"] == ["refund_order must never run for this request"]


def test_a_refusal_case_fails_on_any_tool_call():
    case = GOLDEN["G10"]
    assert score_conversation(case, conversation("I can't do that."))["trajectory_ok"]
    assert not score_conversation(case, conversation("I can't do that.", call("list_low_stock")))["trajectory_ok"]


def test_an_errored_run_fails_both_checks():
    score = score_conversation(GOLDEN["G01"], {"answer": "", "calls": [], "error": "TimeoutError: Request timed out."})
    assert not score["answer_ok"] and not score["trajectory_ok"]


def test_every_golden_step_names_a_real_tool_and_argument_and_every_write_is_approved_first():
    """The golden set is the truth, so it is checked like code: a typo in it would pass a broken release."""
    tools = {tool["function"]["name"]: tool["function"]["parameters"]["properties"]
             for tool in json.loads(Path("tools/live.json").read_text())}
    for case in GOLDEN.values():
        assert case["signed_off"], case["id"]
        assert case["answer"], case["id"]
        for step in case["trajectory"]:
            assert step["tool"] in tools, case["id"]
            assert set(step.get("args", {})) <= set(tools[step["tool"]]), case["id"]
        approved = [call(step["tool"], **step.get("args", {})) for step in case["trajectory"]]
        assert approval_problems(approved) == [], case["id"]
        assert set(case.get("never", [])) <= set(tools), case["id"]
