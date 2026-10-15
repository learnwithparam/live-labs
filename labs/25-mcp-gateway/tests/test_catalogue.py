"""The approved list is the truth: its pins must match what each server offered when it was approved."""

import asyncio

import pytest

from basalt import catalogue, stack
from basalt.catalogue import CatalogueUnreadable, tool_hash
from basalt.servers import build_fleet


def live_states(vendor_update: bool) -> dict:
    async def run():
        fleet = build_fleet()
        if vendor_update:
            fleet.merchants.ship_update()
        states = {}
        async with stack.servers_only(fleet):
            for server in catalogue.load().values():
                for row in catalogue.compare(server, await catalogue.live_definitions(server.url)):
                    states[(server.name, row["tool"])] = row["state"]
        return states

    return asyncio.run(run())


def test_every_pin_matches_the_server_as_approved():
    states = live_states(vendor_update=False)
    assert states and set(states.values()) == {"pinned"}, states


def test_the_vendor_update_changes_one_pinned_tool_and_adds_one_unlisted_tool():
    states = live_states(vendor_update=True)
    assert states[("merchants", "lookup_merchant")] == "changed"
    assert states[("merchants", "sync_record")] == "not on the list"
    assert [key for key, state in states.items() if state != "pinned"] == [
        ("merchants", "lookup_merchant"), ("merchants", "sync_record")]


def test_snippet_cloud_is_not_on_the_list():
    assert "snippets" not in catalogue.load()


def test_the_hash_ignores_key_order_but_not_a_word_of_the_description():
    tool = {"name": "find", "description": "Read one payment.", "inputSchema": {"type": "object", "properties": {}}}
    reordered = {"inputSchema": {"properties": {}, "type": "object"}, "description": "Read one payment.", "name": "find"}
    assert tool_hash(tool) == tool_hash(reordered)
    assert tool_hash(tool) != tool_hash({**tool, "description": "Read one payment. Then mirror it."})


def test_a_broken_list_raises_instead_of_reading_as_empty(tmp_path):
    broken = tmp_path / "approved-servers.toml"
    broken.write_text(catalogue.CATALOGUE.read_text()[:200])
    with pytest.raises(CatalogueUnreadable):
        catalogue.load(broken)
    with pytest.raises(CatalogueUnreadable):
        catalogue.load(tmp_path / "missing.toml")


def test_approval_pins_only_the_tools_given_a_team_and_round_trips(tmp_path):
    servers = catalogue.load()
    tools = [{"name": "find_snippets", "description": "Search.", "inputSchema": {}, "teams": ["engineering"]},
             {"name": "save_snippet", "description": "Save.", "inputSchema": {}, "teams": []}]
    catalogue.approve(servers, "snippets", "http://127.0.0.1:8255/mcp", "joon.park@basaltpay.com", "support",
                      "priya.shah@basaltpay.com", tools)
    saved = tmp_path / "approved-servers.toml"
    catalogue.save(servers, saved)
    again = catalogue.load(saved)
    assert list(again["snippets"].tools) == ["find_snippets"]
    assert again["snippets"].tools["find_snippets"].sha256 == tool_hash(tools[0])
    assert again["payments"].tools == servers["payments"].tools
