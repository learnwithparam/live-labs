"""The metric: labelled audit requests answered correctly and completely, against the grep path."""

from conftest import KEY

from larkspur import naive, signoff
from larkspur.audit import Desk, answer_all, requests_of
from larkspur.config import DATA


def test_the_desk_answers_every_request_from_the_inventory_and_log(fortnight):
    answers = answer_all(Desk(fortnight["systems"], fortnight["log"], KEY), requests_of(DATA))
    assert answers["total"] == 14
    assert answers["answered"] == 14, [row["id"] for row in answers["rows"] if not row["grade"]["correct"]]


def test_grepping_app_logs_answers_far_fewer(fortnight):
    grepped = naive.answer_all(fortnight["app_logs"], requests_of(DATA))
    assert grepped["answered"] <= 3
    rows = {row["id"]: row for row in grepped["rows"]}
    # No log holds a list of systems, and plain files show no sign of an edit: grep says it cannot answer.
    assert "No list of AI systems" in rows["A-01"]["why"] and "edit" in rows["A-14"]["why"]
    # Worse is the answer that looks fine: the fraud log keys on a transaction, not the customer, so the history comes back short.
    assert not rows["A-06"]["correct"] and rows["A-06"]["why"] is None


def test_a_wrong_subject_finds_nothing(fortnight):
    desk = Desk(fortnight["systems"], fortnight["log"], KEY)
    request = next(request for request in requests_of(DATA) if request["kind"] == "subject_history")
    request = {**request, "params": {**request["params"], "subject": "LB-00000"}}
    assert desk.answer(request)["result"]["requests"] == []


def test_signing_waits_for_the_finding(fortnight, tmp_path):
    answers = answer_all(Desk(fortnight["systems"], fortnight["log"], KEY), requests_of(DATA))
    from larkspur.inventory import findings

    found = findings(fortnight["systems"])
    assert signoff.blockers(answers, found, {}) == ["Finding on dispute-mailer is not decided"]
    decisions = signoff.decide(tmp_path / "decisions.json", "dispute-mailer", "keep-blocked", "Ines", found)
    pack = signoff.sign(tmp_path / "pack.json", answers, found, decisions, "Ines")
    assert pack["anchor"] == answers["chain"]["head"] and len(pack["answers"]) == 14
