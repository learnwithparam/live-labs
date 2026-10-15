"""The sync: three access lists in, one set of tuples out, and a bad name holds only its own document."""

import json
import re
from pathlib import Path

import pytest

from copperline import sync
from copperline.sync import build

AUDIT_MAP = {"SEC-61": {"map": {"ext-auditors-2026": "security"}}}


def viewers_of(tuples: list[tuple], document_id: str) -> set[str]:
    return {subject for subject, relation, target in tuples if target == f"document:{document_id}" and relation == "viewer"}


def test_every_source_document_becomes_tuples_and_only_the_bad_ticket_is_held():
    built = build(admin_decisions={})
    assert [held["document"] for held in built["held"]] == ["SEC-61"]
    held = built["held"][0]
    assert "ext-auditors-2026" in held["reasons"][0]
    # Fail closed: the held ticket is visible to the person who filed it and nobody else.
    assert viewers_of(built["tuples"], "SEC-61") == {f"user:{held['owner']}"}
    assert not any("ext-auditors" in subject for subject, _, _ in built["tuples"])


def test_an_admin_mapping_releases_the_ticket_to_the_named_group():
    built = build(admin_decisions=AUDIT_MAP)
    assert built["held"] == []
    assert "group:security#member" in viewers_of(built["tuples"], "SEC-61")


def test_keeping_it_private_keeps_it_held_and_owner_only():
    built = build(admin_decisions={"SEC-61": {"keep": True}})
    assert built["held"][0]["decision"] == "kept private"
    assert len(viewers_of(built["tuples"], "SEC-61")) == 1


def test_anyone_with_the_link_reaches_only_the_people_who_opened_it():
    tuples = build(admin_decisions={})["tuples"]
    link = "link:drv-larch-term-sheet"
    assert (link, "shared_link", "document:drv-larch-term-sheet") in tuples
    assert {subject for subject, relation, target in tuples if target == link} == {"user:tom"}


def test_a_restricted_wiki_page_stops_inheriting_its_space():
    wiki = json.loads(Path("data/sources/wiki.json").read_text())
    restricted = [page["id"] for page in wiki["pages"] if page["restrictions"]]
    assert restricted
    tuples = build(admin_decisions={})["tuples"]
    for page_id in restricted:
        assert not any(relation == "parent" and target == f"document:{page_id}" for _, relation, target in tuples)


def test_contractors_are_not_all_staff():
    tuples = build(admin_decisions={})["tuples"]
    all_staff = {subject for subject, relation, target in tuples if target == "group:all-staff" and relation == "member"}
    assert "group:contractors#member" not in all_staff and "user:jo" not in all_staff


def test_a_bad_name_on_a_folder_stops_the_sync(monkeypatch):
    real_load = sync.load

    def broken(name: str) -> dict:
        exported = real_load(name)
        if name == "sources/drive.json":
            exported["folders"][0]["permissions"].append(
                {"type": "group", "role": "reader", "emailAddress": "everyone-ever@example.com"})
        return exported

    monkeypatch.setattr(sync, "load", broken)
    # A container with a bad name would expose or hide everything in it, so nothing is loaded.
    with pytest.raises(SystemExit, match="container"):
        build(admin_decisions={})


def test_every_relation_in_the_dsl_is_in_the_loaded_json_model():
    """`data/model.json` is generated from `data/model.fga`; this fails when someone edits one and not the other."""
    dsl = Path("data/model.fga").read_text()
    written = {(kind, relation) for kind, block in re.findall(r"^type (\w+)\n((?:  .*\n?)*)", dsl, re.M)
               for relation in re.findall(r"define (\w+):", block)}
    model = json.loads(Path("data/model.json").read_text())
    loaded = {(definition["type"], relation) for definition in model["type_definitions"]
              for relation in definition.get("relations") or {}}
    assert written == loaded
