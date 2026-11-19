from brickhaven_slice.repair import check_work_order, parse_work_order

GOOD = '{"trade": "plumbing", "urgency": "high", "unit": "4B", "safety_flag": false, "access_window": null}'


def test_clean_json_parses():
    order, problems = parse_work_order(GOOD)
    assert problems == [] and order["unit"] == "4B"


def test_a_fence_a_preamble_and_a_trailing_comma_are_repaired_without_a_model():
    assert parse_work_order("```json\n" + GOOD + "\n```")[0] is not None
    assert parse_work_order("Here you go: " + GOOD + " Hope that helps.")[0] is not None
    assert parse_work_order(GOOD[:-1] + ",}")[0] is not None


def test_prose_is_not_json_and_says_so():
    order, problems = parse_work_order("The tap is dripping.")
    assert order is None and problems[0].startswith("not valid JSON")


def test_each_contract_breach_is_named():
    broken = {"trade": "carpentry", "urgency": "now", "unit": 4, "safety_flag": "yes"}
    problems = check_work_order(broken)
    assert "missing access_window" in problems
    assert any("trade 'carpentry'" in problem for problem in problems)
    assert any("urgency 'now'" in problem for problem in problems)
    assert "safety_flag is not true or false" in problems and "unit is not text or null" in problems
    assert check_work_order([1]) == ["the answer is not a JSON object"]
