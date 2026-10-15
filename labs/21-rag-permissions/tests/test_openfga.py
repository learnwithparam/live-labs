"""The access lists against a real OpenFGA server: every pair's denied user is denied, its allowed user allowed.

Skipped when no OpenFGA answers at FGA_API_URL; start one with the docker command in the README.
"""

import urllib.request

import pytest

from copperline.access import Authorizer, describe, load_store
from copperline.config import fga_url
from copperline.leaks import pairs
from copperline.sync import build, labels, people


def reachable() -> bool:
    try:
        with urllib.request.urlopen(f"{fga_url()}/healthz", timeout=2) as response:
            return b"SERVING" in response.read()
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not reachable(), reason="no OpenFGA server at FGA_API_URL")


@pytest.fixture(scope="module")
def authorizer(tmp_path_factory):
    store_file = tmp_path_factory.mktemp("fga") / "fga.json"
    load_store(build(admin_decisions={})["tuples"], store_file)
    return Authorizer(store_file)


@pytest.mark.parametrize("pair", pairs(), ids=lambda pair: pair["id"])
def test_each_pair_is_denied_and_allowed_as_its_source_says(authorizer, pair):
    assert authorizer.allowed(pair["denied"], [pair["document"]]) == set()
    assert authorizer.allowed(pair["allowed"], [pair["document"]]) == {pair["document"]}


def test_list_objects_and_batch_check_agree_for_every_person(authorizer):
    every = [document for document in labels() if document.startswith("document:")]
    every = [document.removeprefix("document:") for document in every]
    for user in people():
        assert authorizer.viewable(user) == authorizer.allowed(user, every), user


def test_the_held_ticket_is_visible_to_its_reporter_alone(authorizer):
    held = build(admin_decisions={})["held"][0]
    assert {user for user in people() if authorizer.allowed(user, [held["document"]])} == {held["owner"]}


def test_why_walks_from_the_person_through_nested_groups_and_a_folder(authorizer):
    path = authorizer.why("dev", "drv-brand-brief")
    assert path[0]["subject"] == "user:dev" and path[-1]["object"] == "document:drv-brand-brief"
    lines = describe(path, labels())
    assert any("Security" in line for line in lines) and any("folder" in line for line in lines)
