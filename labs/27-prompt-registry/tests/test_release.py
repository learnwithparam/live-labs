"""Releasing by label: the gate fails closed, an override needs a person and a reason, and rollback is one move."""

import pytest

from driftwell.registry import holder, versions
from driftwell.release import ReleaseLog, Refused, Scores, gate, note_reply, promote, rollback


@pytest.fixture
def log(tmp_path):
    started = ReleaseLog(tmp_path / "releases.json")
    started.add(at=0.0, kind="import", label="production", came_from=None, to=1, by="engineering", score=None,
                reason=None, served_after=None)
    return started


def production(client) -> int:
    return holder(versions(client), "production").number


def test_a_version_above_the_bar_ships_when_a_person_promotes_it(registry, scores, log):
    event = promote(registry, log, scores, 2, "Maya Ortiz")
    assert production(registry) == 2
    assert (event["kind"], event["came_from"], event["by"], event["score"]) == ("promote", 1, "Maya Ortiz", 1.0)


def test_a_promotion_with_no_name_is_refused(registry, scores, log):
    with pytest.raises(Refused, match="name of the person"):
        promote(registry, log, scores, 2, "  ")
    assert production(registry) == 1


def test_a_version_below_the_bar_is_blocked_and_production_does_not_move(registry, scores, log):
    with pytest.raises(Refused, match="1 of 16 golden conversations, below the bar of 14"):
        promote(registry, log, scores, 3, "Sam Lee")
    assert production(registry) == 1


def test_an_unscored_version_is_blocked(registry, log, tmp_path):
    assert gate(None) == "it has not been scored on the golden set"
    with pytest.raises(Refused, match="not been scored"):
        promote(registry, log, Scores(tmp_path / "empty.json"), 2, "Maya Ortiz")


def test_an_override_ships_below_the_bar_and_records_who_and_why(registry, scores, log):
    event = promote(registry, log, scores, 3, "Sam Lee", override="Brand team wants the warmer tone live today")
    assert production(registry) == 3
    assert (event["kind"], event["by"], event["reason"]) == ("override", "Sam Lee", "Brand team wants the warmer tone live today")


def test_rollback_returns_production_to_the_version_it_replaced(registry, scores, log):
    promote(registry, log, scores, 2, "Maya Ortiz")
    promote(registry, log, scores, 3, "Sam Lee", override="ship it")
    event = rollback(registry, log, "Maya Ortiz")
    assert production(registry) == 2
    assert (event["kind"], event["came_from"], event["to"]) == ("rollback", 3, 2)


def test_rollback_with_no_earlier_production_is_refused(registry, log):
    with pytest.raises(Refused, match="no earlier production version"):
        rollback(registry, log, "Maya Ortiz")
    assert production(registry) == 1


def test_the_rollback_clock_stops_on_the_first_reply_from_the_restored_version(registry, scores, log):
    promote(registry, log, scores, 2, "Maya Ortiz")
    promote(registry, log, scores, 3, "Sam Lee", override="ship it")
    event = rollback(registry, log, "Maya Ortiz", now=lambda: 100.0)
    assert note_reply(log, {"version": 3, "at": 104.0}) is None, "a reply from the bad version does not count"
    assert note_reply(log, {"version": 2, "at": 99.0}) is None, "a reply from before the move does not count"
    closed = note_reply(log, {"version": 2, "at": 111.3})
    assert closed is event and closed["served_after"] == 11.3
    assert note_reply(log, {"version": 2, "at": 120.0}) is None, "the clock closes once"


def test_a_second_rollback_does_not_reship_the_version_the_first_one_removed(registry, scores, log):
    promote(registry, log, scores, 2, "Maya Ortiz")
    promote(registry, log, scores, 3, "Sam Lee", override="ship it")
    rollback(registry, log, "Maya Ortiz")
    event = rollback(registry, log, "Maya Ortiz")
    assert (event["came_from"], event["to"]) == (2, 1)
    assert production(registry) == 1


def test_a_rollback_with_no_name_is_refused(registry, scores, log):
    promote(registry, log, scores, 2, "Maya Ortiz")
    with pytest.raises(Refused, match="name of the person"):
        rollback(registry, log, "")
    assert production(registry) == 2
