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


def edited_export(monkeypatch, source: str, edit) -> None:
    """Change one source export the way a real system might send it, for the next build."""
    real_load = sync.load

    def edited(name: str) -> dict:
        exported = real_load(name)
        if name == f"sources/{source}.json":
            edit(exported)
        return exported

    monkeypatch.setattr(sync, "load", edited)


def test_an_unknown_security_level_holds_the_ticket_instead_of_opening_it_to_the_project(monkeypatch):
    def rename_level(tickets: dict) -> None:
        issue = next(issue for issue in tickets["issues"] if issue["security_level"] == "hr-confidential")
        issue["security_level"] = "hr-confidental"

    edited_export(monkeypatch, "tickets", rename_level)
    built = build(admin_decisions=AUDIT_MAP)
    [held] = built["held"]
    assert "hr-confidental" in held["reasons"][0]
    assert viewers_of(built["tuples"], held["document"]) == {f"user:{held['owner']}"}
    assert not any(relation == "parent" and target == f"document:{held['document']}" for _, relation, target in built["tuples"])


def test_a_domain_grant_to_another_company_is_not_all_staff(monkeypatch):
    def share_outside(drive: dict) -> None:
        drive["files"][0]["permissions"].append({"type": "domain", "role": "reader", "domain": "studiokim.design"})

    edited_export(monkeypatch, "drive", share_outside)
    built = build(admin_decisions=AUDIT_MAP)
    [held] = built["held"]
    assert "studiokim.design" in held["reasons"][0]
    assert "group:all-staff#member" not in viewers_of(built["tuples"], held["document"])


def test_a_held_document_goes_to_its_owner_by_directory_lookup_not_by_email_prefix(monkeypatch):
    # Jo's email is jo.kim@studiokim.design; the user id is jo. Cutting the email at "@" would name nobody.
    def jo_owns_a_badly_shared_file(drive: dict) -> None:
        drive["files"][0]["owner"] = "jo.kim@studiokim.design"
        drive["files"][0]["permissions"].append({"type": "user", "role": "reader", "emailAddress": "nobody@example.com"})

    edited_export(monkeypatch, "drive", jo_owns_a_badly_shared_file)
    built = build(admin_decisions=AUDIT_MAP)
    [held] = built["held"]
    assert held["owner"] == "jo" and viewers_of(built["tuples"], held["document"]) == {"user:jo"}


def test_a_held_document_whose_owner_is_unknown_is_seen_by_no_one(monkeypatch):
    def stranger_owns_a_file(drive: dict) -> None:
        drive["files"][0]["owner"] = "stranger@example.com"

    edited_export(monkeypatch, "drive", stranger_owns_a_file)
    built = build(admin_decisions=AUDIT_MAP)
    [held] = built["held"]
    assert held["owner"] is None and viewers_of(built["tuples"], held["document"]) == set()
