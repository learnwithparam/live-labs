"""Routing rules and the grade, run end to end on the scripted model."""

import asyncio

import pytest

from saltmarsh import assess as assessing
from saltmarsh.claims import rates
from saltmarsh.triage import route

from fakes import FakeModel


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(assessing, "OUT", tmp_path)
    return tmp_path


def test_a_perfect_reader_passes_every_bill_and_routes_like_the_adjuster(out):
    run = asyncio.run(assessing.assess(FakeModel(), "test-model"))
    score = run["score"]
    assert score["within_tolerance"] == score["billed"] == 6
    assert score["routed_right"] == score["claims"] == 10
    assert score["reasons_caught"] == score["expected_adjuster"] == 4
    assert score["panels"]["recall"] == score["panels"]["precision"] == 1.0
    assert (out / "run.json").exists()
    routes = {result["claim"]: {reason["code"] for reason in result["route"]["reasons"]} for result in run["results"]}
    assert routes["SM-1050"] >= {"photos_disagree"} and routes["SM-1048"] == {"prior_damage"}


def test_old_rust_routes_the_claim_but_is_not_priced(out):
    run = asyncio.run(assessing.assess(FakeModel(), "test-model"))
    saab = next(result for result in run["results"] if result["claim"] == "SM-1048")
    assert saab["estimate"]["lines"] == [] and saab["route"]["to"] == "adjuster"


def test_a_free_text_estimate_is_graded_by_the_same_tolerance(out):
    run = asyncio.run(assessing.naive(FakeModel("Somewhere around £1,000."), "test-model"))
    assert run["billed"] == 6
    assert run["within_tolerance"] == sum(abs(1000 - row["bill"]) <= 0.2 * row["bill"] for row in run["estimates"])


def test_photos_showing_no_damage_go_to_a_person_not_to_a_zero_payout():
    readings = [{"photo": "P10", "usable": True, "quality_problem": "", "findings": []}]
    decision = route(readings, {"same_vehicle": True}, {"lines": [], "total": 0, "share_of_value": 0}, rates())
    assert decision["to"] == "adjuster" and decision["reasons"][0]["code"] == "image_quality"


def test_an_estimate_over_the_write_off_share_is_a_total_loss():
    estimate = {"lines": [{}], "total": 900, "share_of_value": 0.61}
    decision = route([], {"same_vehicle": True}, estimate, rates())
    assert [reason["code"] for reason in decision["reasons"]] == ["total_loss"]
