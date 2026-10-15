"""Tests that need no key: the reconcile rule, the repair loop, the receipt formats and env.example."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from receipts import cli
from receipts.config import DEFAULT_BASE_URL, DEFAULT_MODEL
from receipts.extract import ExtractionFailed, extract, read_receipt, response_format
from receipts.schema import Receipt
from receipts.match import load_charges
from receipts.serve import Desk, serve

LAB = Path(__file__).parent.parent
WRONG = json.loads((LAB / "data/ledger-wrong-total.json").read_text())
CHARGES = load_charges(LAB / "data/card-transactions.csv")


def test_total_that_does_not_add_up_is_rejected():
    with pytest.raises(ValidationError, match="add up to 16.70"):
        Receipt.model_validate(WRONG)


def test_total_that_adds_up_is_accepted():
    assert Receipt.model_validate({**WRONG, "total": 16.70}).total == 16.70


def test_included_tax_is_not_added_again():
    berlin = {**WRONG, "currency": "EUR", "tip": 0, "tax": 2.15, "tax_included": True, "total": 13.50}
    assert Receipt.model_validate(berlin).expected_total() == 13.50


class FakeClient:
    """Replies with each canned JSON in turn and records what it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.sent.append(kwargs["messages"])
        reply = self.replies.pop(0)
        content = reply if isinstance(reply, str) else json.dumps(reply)
        usage = SimpleNamespace(cost=0.001)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)


def test_validation_error_is_sent_back_and_repaired():
    client = FakeClient(WRONG, {**WRONG, "total": 16.70})
    result = extract("receipt text", client, "any-model")
    assert result.attempts == 2
    assert "add up to 16.70" in client.sent[1][-1]["content"]


def test_gives_up_after_the_last_attempt_and_keeps_its_cost():
    with pytest.raises(ExtractionFailed) as failed:
        extract("receipt text", FakeClient(WRONG, WRONG), "any-model")
    assert failed.value.cost == pytest.approx(0.002)


def test_the_receipts_arrive_as_photos_pdfs_and_text():
    kinds = {path.suffix for path in cli.receipts_in(LAB / "data/receipts")}
    assert kinds == {".jpg", ".pdf", ".txt"}


def test_a_photo_and_a_pdf_are_sent_as_data_urls():
    photo = read_receipt(LAB / "data/receipts/coffee.jpg")[0]
    pdf = read_receipt(LAB / "data/receipts/hotel.pdf")[0]
    assert photo["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert pdf["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert isinstance(read_receipt(LAB / "data/receipts/taxi.txt"), str)


def test_naive_reports_a_fenced_reply_and_a_total_it_cannot_book(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.txt").write_text("receipt")
    fenced = '```json\n{"store": "Blue Door", "total": "$16.70"}\n```'
    monkeypatch.setattr(cli, "client_and_model", lambda: (FakeClient(fenced), "any-model"))
    cli.naive_all(tmp_path)
    assert "1/1 not JSON as sent, 1 different shapes, 1 with no number under `total`" in capsys.readouterr().out


def test_the_desk_shows_the_repair_and_the_matched_charge():
    desk = Desk(LAB / "data/receipts", CHARGES, FakeClient(WRONG, {**WRONG, "total": 16.70}), "any-model")
    server = serve(desk, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        request = urllib.request.Request(f"{base}/api/extract/coffee.jpg", method="POST")
        outcome = json.loads(urllib.request.urlopen(request).read())
        listing = json.loads(urllib.request.urlopen(f"{base}/api/receipts").read())
        page = urllib.request.urlopen(base).read().decode()
    finally:
        server.shutdown()
    first, second = outcome["attempts"]
    assert "add up to 16.70" in first["error"] and first["sum"]["expected"] == 16.70
    assert second["error"] is None and outcome["status"] == "matched"
    assert outcome["charge"]["id"] == "t03" and set(outcome["checks"]) == {"amount", "date", "merchant"}
    assert listing["results"]["coffee.jpg"]["charge"]["id"] == "t03" and len(listing["charges"]) == 10
    assert "Receipt matcher" in page and 'id="tally"' in page and "A person decides" in page


def test_the_desk_serves_only_the_receipts():
    server = serve(Desk(LAB / "data/receipts", CHARGES, FakeClient(), "any-model"), 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError, match="404"):
            urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/files/..%2Fledger-wrong-total.json")
    finally:
        server.shutdown()


@pytest.mark.parametrize("headers", [{"Host": "attacker.example:80"}, {"Origin": "http://attacker.example"}])
def test_the_desk_refuses_other_sites(headers):
    server = serve(Desk(LAB / "data/receipts", CHARGES, FakeClient(), "any-model"), 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/naive/coffee.jpg", headers=headers, method="POST")
        with pytest.raises(urllib.error.HTTPError, match="403"):
            urllib.request.urlopen(request)
    finally:
        server.shutdown()


def test_env_example_matches_the_code_defaults():
    env = dict(
        line.split("=", 1) for line in (LAB / "env.example").read_text().splitlines() if "=" in line and not line.startswith("#")
    )
    assert env["LLM_MODEL"] == DEFAULT_MODEL
    assert env["LLM_BASE_URL"] == DEFAULT_BASE_URL


def objects_in(node):
    """Every object in a JSON schema, nested definitions included."""
    if isinstance(node, dict):
        if "properties" in node:
            yield node
        for value in node.values():
            yield from objects_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from objects_in(value)


def test_the_schema_meets_openai_strict_mode():
    # OpenAI models reject a schema where an object leaves a property optional or allows extra keys.
    schema = response_format()["json_schema"]["schema"]
    objects = list(objects_in(schema))
    assert len(objects) >= 2, "the receipt and its line items"
    for found in objects:
        assert found["required"] == list(found["properties"])
        assert found["additionalProperties"] is False
