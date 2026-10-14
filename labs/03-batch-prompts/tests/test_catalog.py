"""Tests that need no key: the batch file, the parse and retry, the checkpoint, the score, the batch API calls,
the page and env.example."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from catalog.config import DEFAULT_BASE_URL, DEFAULT_MODEL
from catalog.fill import INSTRUCTIONS, batch_lines, collect, one_lines, parse, read_jsonl, run_lines, settle, skus_in, submit
from catalog.schema import Attributes
from catalog.score import accuracy, load_products, load_supplied, tally, why_a_person
from catalog.serve import Shelf, serve

LAB = Path(__file__).parent.parent
CATALOGUE, SUPPLIED = LAB / "data/catalogue.csv", LAB / "data/retailer-attributes.csv"
PRODUCTS = load_products(CATALOGUE)
BY_SKU = {p.sku: p for p in PRODUCTS}
RETAILER = load_supplied(SUPPLIED)
DROPPED = "SKU10005"  # the sku the fake leaves out of any pack reply, as a real model sometimes does


def answer(sku: str) -> dict:
    """The retailer's attributes where it supplied them; elsewhere a pantry item, vegan only with ingredients."""
    row = RETAILER.get(sku)
    if row:
        return {**row, "size_value": float(row["size_value"]), "organic": row["organic"] == "true", "vegan": row["vegan"] or "unknown"}
    vegan = "yes" if "Ingredients:" in BY_SKU[sku].description else "unknown"
    return {"sku": sku, "category": "pantry", "size_value": 100.0, "size_unit": "g", "organic": False, "vegan": vegan}


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **body):
        self.calls += 1
        skus = skus_in(body)
        if len(skus) == 1:
            content = json.dumps(answer(skus[0]))
        else:
            content = json.dumps({"products": [answer(s) for s in skus if s != DROPPED]})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=SimpleNamespace(cost=0.0001))


def test_the_batch_file_packs_products_under_one_copy_of_the_instructions():
    lines = batch_lines(PRODUCTS, "any-model", 24)
    assert len(lines) == 10 and [line["custom_id"] for line in lines][:2] == ["pack-0000", "pack-0001"]
    for line in lines:
        assert line["method"] == "POST" and line["url"] == "/v1/chat/completions"
        assert line["body"]["messages"][0]["content"] == INSTRUCTIONS and len(skus_in(line["body"])) == 24
    assert sum(len(skus_in(line["body"])) for line in lines) == len(PRODUCTS)
    assert len(one_lines(PRODUCTS, "any-model")) == len(PRODUCTS)


def test_a_reply_that_drops_or_mangles_a_sku_names_it():
    filled, dropped = parse(json.dumps({"products": [answer("SKU10000")]}), ["SKU10000", "SKU10001"])
    assert [a.sku for a in filled] == ["SKU10000"] and dropped == ["SKU10001"]
    assert parse("not json", ["SKU10000"]) == ([], ["SKU10000"])
    stranger = json.dumps({"products": [answer("SKU10000") | {"sku": "SKU99999"}]})
    assert parse(stranger, ["SKU10000"]) == ([], ["SKU10000"])


def test_a_rerun_skips_what_the_checkpoint_already_holds(tmp_path):
    lines, done, client = batch_lines(PRODUCTS, "any-model", 24), tmp_path / "done.jsonl", FakeClient()
    assert run_lines(lines, client, done) == {"sent": 10, "skipped": 0}
    assert run_lines(lines, client, done) == {"sent": 0, "skipped": 10} and client.calls == 10
    done.write_text("".join(json.dumps(row) + "\n" for row in read_jsonl(done)[:7]))  # the run died at pack 8
    assert run_lines(lines, client, done) == {"sent": 3, "skipped": 7}


def test_a_dropped_sku_is_retried_once_on_its_own(tmp_path):
    done, client = tmp_path / "done.jsonl", FakeClient()
    run_lines(batch_lines(PRODUCTS, "any-model", 24), client, done)
    filled, cost = settle(done, BY_SKU, client, "any-model")
    assert DROPPED in filled and len(filled) == len(PRODUCTS) and client.calls == 11
    assert f"retry-{DROPPED}" in {row["custom_id"] for row in read_jsonl(done)}
    settle(done, BY_SKU, client, "any-model")
    assert client.calls == 11 and cost == pytest.approx(0.0011)


def test_accuracy_counts_only_what_the_retailer_supplied():
    filled = {sku: Attributes(**answer(sku)) for sku in RETAILER}
    right, total = accuracy(filled, RETAILER)
    assert right == total == sum(1 for row in RETAILER.values() for k, v in row.items() if k != "sku" and v != "")
    wrong = {**filled, "SKU10000": filled["SKU10000"].model_copy(update={"size_value": 1.0})}
    assert accuracy(wrong, RETAILER) == (total - 1, total)
    assert accuracy({}, RETAILER) == (0, total)


def test_a_person_is_needed_only_when_vegan_cannot_be_read_or_nothing_came_back():
    product = BY_SKU["SKU10000"]
    food = Attributes(**answer("SKU10000"))
    assert why_a_person(product, food) is None
    assert "vegan cannot be set" in why_a_person(product, food.model_copy(update={"vegan": "unknown"}))
    towels = food.model_copy(update={"category": "household", "vegan": "unknown"})
    assert why_a_person(product, towels) is None
    assert why_a_person(product, None).startswith("no reply")
    result = tally(PRODUCTS, {}, RETAILER, 0.24)
    assert result.filled == 0 and len(result.person) == 240 and result.per_thousand == pytest.approx(1.0)


class FakeBatches:
    """An OpenAI-compatible files and batches API: takes the file, then hands back one result per line."""

    def __init__(self):
        self.status, self.uploaded = "in_progress", b""
        self.files = SimpleNamespace(create=self.upload, content=self.content)
        self.batches = SimpleNamespace(create=self.start, retrieve=self.retrieve)

    def upload(self, file, purpose):
        assert purpose == "batch"
        self.uploaded = file[1]
        return SimpleNamespace(id="file-1")

    def start(self, input_file_id, endpoint, completion_window):
        assert (input_file_id, endpoint, completion_window) == ("file-1", "/v1/chat/completions", "24h")
        return SimpleNamespace(id="batch-1")

    def retrieve(self, batch_id):
        return SimpleNamespace(status=self.status, output_file_id="file-2")

    def content(self, file_id):
        rows = []
        for line in self.uploaded.decode().splitlines():
            request = json.loads(line)
            reply = {"products": [answer(s) for s in skus_in(request["body"])]}
            body = {"choices": [{"message": {"content": json.dumps(reply)}}], "usage": {"cost": 0.00005}}
            rows.append(json.dumps({"custom_id": request["custom_id"], "response": {"body": body}}))
        return SimpleNamespace(text="\n".join(rows))


def test_the_same_file_goes_to_a_batch_api_and_comes_back_as_checkpoint_rows():
    lines, api = batch_lines(PRODUCTS, "any-model", 24), FakeBatches()
    assert submit(lines, api) == "batch-1" and len(api.uploaded.decode().splitlines()) == 10
    assert collect("batch-1", api) is None
    api.status = "completed"
    rows = collect("batch-1", api)
    assert [r["custom_id"] for r in rows] == [line["custom_id"] for line in lines]
    kept, dropped = parse(rows[0]["reply"], skus_in(lines[0]["body"]))
    assert len(kept) == 24 and not dropped


def start(shelf: Shelf):
    server = serve(shelf, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_the_page_fills_both_ways_and_shows_who_needs_a_person(tmp_path):
    client = FakeClient()
    server, base = start(Shelf(CATALOGUE, SUPPLIED, client, "any-model", tmp_path))
    try:
        shelf = json.loads(urllib.request.urlopen(f"{base}/api/shelf").read())
        post = lambda way: json.loads(urllib.request.urlopen(urllib.request.Request(f"{base}/api/fill/{way}", method="POST")).read())
        one, packed, again = post("one"), post("packed"), post("packed")
        page = urllib.request.urlopen(base).read().decode()
    finally:
        server.shutdown()
    assert len(shelf["products"]) == 240 and len(shelf["supplied"]) == 48
    assert (one["requests"], packed["requests"], again["sent"]) == (240, 10, 0)
    assert packed["per_thousand"] < one["per_thousand"] and packed["right"] == packed["total"]
    assert packed["person"] and all("vegan cannot be set" in why for why in packed["person"].values())
    assert packed["filled"] + len(packed["person"]) == 240
    assert 'id="tally"' in page and "A person decides" in page and "Filled with no person" in page


@pytest.mark.parametrize("headers", [{"Host": "attacker.example:80"}, {"Origin": "http://attacker.example"}])
def test_the_page_refuses_other_sites(headers, tmp_path):
    server, base = start(Shelf(CATALOGUE, SUPPLIED, FakeClient(), "any-model", tmp_path))
    try:
        request = urllib.request.Request(f"{base}/api/fill/packed", headers=headers, method="POST")
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
