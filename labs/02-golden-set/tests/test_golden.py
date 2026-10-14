"""Tests that need no key: the golden set, the score, the gate, the bench page and env.example."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from postings.config import DEFAULT_BASE_URL, DEFAULT_MODEL
from postings.schema import FIELDS, Posting, same
from postings.score import accuracy, gate, load_golden
from postings.serve import Bench, serve

LAB = Path(__file__).parent.parent
GOLDEN = load_golden(LAB / "data/golden.jsonl")
POSTINGS = {name: (LAB / "data/postings" / name).read_text() for name in GOLDEN}
# What the shorter prompt gets wrong in the fake: lakh read as plain rupees.
LAKH = {"salary_min": 28, "salary_max": 35}


class FakeClient:
    """Answers each posting with its approved fields, except the shorter prompt misreads lumen's lakh."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        system, posting = (m["content"] for m in kwargs["messages"])
        name = next(n for n, text in POSTINGS.items() if text == posting)
        fields = dict(GOLDEN[name])
        if name == "lumen-swe2.txt" and len(system.split()) < 30:
            fields |= LAKH
        content = json.dumps(fields)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=SimpleNamespace(cost=0.0001))


def test_the_golden_set_labels_every_posting_in_the_filter_shape():
    assert set(GOLDEN) == {p.name for p in (LAB / "data/postings").glob("*.txt")}
    for approved in GOLDEN.values():
        Posting.model_validate(approved)


def test_numbers_match_to_the_unit_and_codes_ignore_case():
    assert same("salary_min", 2800000.0, 2800000) and not same("salary_min", 28, 2800000)
    assert same("currency", "inr", "INR") and same("currency", None, None) and not same("salary_max", None, 42000)


def test_a_field_that_gets_worse_blocks_the_change_and_names_the_posting():
    before = {name: dict(fields) for name, fields in GOLDEN.items()}
    after = {**before, "lumen-swe2.txt": {**before["lumen-swe2.txt"], **LAKH}}
    verdict = gate(GOLDEN, before, after)
    assert verdict.blocked and verdict.worse == ["salary_min", "salary_max"]
    assert verdict.needs_person == ["lumen-swe2.txt"] and accuracy(GOLDEN, after)["salary_min"] == 13


def test_a_change_that_only_fixes_passes_the_gate():
    before = {**GOLDEN, "harbor-support-parttime.txt": {**GOLDEN["harbor-support-parttime.txt"], "seniority": "junior"}}
    verdict = gate(GOLDEN, before, GOLDEN)
    assert not verdict.blocked and not verdict.needs_person
    assert [(c.posting, c.field) for c in verdict.fixed] == [("harbor-support-parttime.txt", "seniority")]


def start(bench: Bench):
    server = serve(bench, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_the_bench_scores_two_prompts_and_shows_what_needs_a_person():
    server, base = start(Bench(LAB / "prompts", LAB / "data/postings", LAB / "data/golden.jsonl", FakeClient(), "any-model"))
    try:
        listing = json.loads(urllib.request.urlopen(f"{base}/api/bench").read())
        request = urllib.request.Request(f"{base}/api/gate/shipped/shorter", method="POST")
        verdict = json.loads(urllib.request.urlopen(request).read())
        page = urllib.request.urlopen(base).read().decode()
    finally:
        server.shutdown()
    assert {"shipped", "shorter", "years-rule"} <= set(listing["prompts"]) and len(listing["postings"]) == 14
    assert verdict["blocked"] and verdict["needs_person"] == ["lumen-swe2.txt"]
    assert verdict["after"]["salary_min"] == 13 and verdict["before"] == {f: 14 for f in FIELDS}
    assert 'id="tally"' in page and "A person decides" in page


@pytest.mark.parametrize("headers", [{"Host": "attacker.example:80"}, {"Origin": "http://attacker.example"}])
def test_the_bench_refuses_other_sites(headers):
    server, base = start(Bench(LAB / "prompts", LAB / "data/postings", LAB / "data/golden.jsonl", FakeClient(), "any-model"))
    try:
        request = urllib.request.Request(f"{base}/api/gate/shipped/shorter", headers=headers, method="POST")
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
