"""The gateway fails closed: a refused call spends nothing, and every call leaves a record."""

from conftest import KEY
from fakes import FakeOpenAI

from larkspur import inventory
from larkspur.auditlog import AuditLog
from larkspur.gateway import governed_call


def request_for(system, text="Where is my refund?", model=None, at="2026-09-03T10:00:00Z"):
    request = {"id": "T-1", "at": at, "system": system, "subject": "LB-10001", "text": text}
    if model:
        request["model"] = model
    return request


def test_customer_facing_reply_starts_with_the_disclosure(tmp_path):
    systems, client = inventory.load(), FakeOpenAI()
    log = AuditLog(tmp_path / "audit.sqlite")
    outcome = governed_call(request_for("help-chat"), systems, client, log, KEY)
    assert outcome.status == "answered"
    assert outcome.delivered.startswith(systems["help-chat"].disclosure + "\n\n")
    assert outcome.record["disclosure.shown"] is True
    assert outcome.record["gen_ai.prompt.name"] == "help-chat@3"
    log.close()


def test_refusals_never_reach_the_model(tmp_path):
    systems, client = inventory.load(), FakeOpenAI()
    log = AuditLog(tmp_path / "audit.sqlite")
    refused = [request_for("promo-writer"), request_for("dispute-mailer"),
               request_for("fraud-scorer", model="openai/gpt-6-sol"),
               request_for("help-chat", at="2026-08-01T10:00:00Z")]
    reasons = [governed_call(request, systems, client, log, KEY).reason for request in refused]
    assert client.seen == []
    assert "not in the AI inventory" in reasons[0]
    assert "no AI disclosure" in reasons[1]
    assert "not a model the inventory lists" in reasons[2]
    assert "no prompt version" in reasons[3]
    assert [record["outcome"] for record in log.query()] == ["refused"] * 4
    log.close()


def test_a_failed_call_is_logged_with_its_error(tmp_path):
    systems, client = inventory.load(), FakeOpenAI(fail_on="refund")
    log = AuditLog(tmp_path / "audit.sqlite")
    outcome = governed_call(request_for("help-chat"), systems, client, log, KEY)
    assert outcome.status == "failed" and outcome.delivered is None
    assert log.query()[0]["error.type"] == "TimeoutError"
    log.close()


def test_the_subject_is_stored_as_a_pseudonym(tmp_path):
    systems = inventory.load()
    log = AuditLog(tmp_path / "audit.sqlite")
    governed_call(request_for("help-chat"), systems, FakeOpenAI(), log, KEY)
    log.close()
    assert b"LB-10001" not in (tmp_path / "audit.sqlite").read_bytes()
