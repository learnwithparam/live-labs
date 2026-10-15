"""The inventory is code: a malformed or incomplete entry stops the build, not the audit."""

import shutil

import pytest

from larkspur import inventory
from larkspur.config import DATA


def copy_of_data(tmp_path):
    target = tmp_path / "data"
    shutil.copytree(DATA, target)
    return target


def test_the_real_inventory_loads_with_one_finding():
    systems = inventory.load()
    assert sorted(systems) == ["dispute-mailer", "fraud-scorer", "help-chat", "staff-search"]
    found = inventory.findings(systems)
    assert [finding["system"] for finding in found] == ["dispute-mailer"]
    assert "no AI disclosure" in found[0]["reason"] and found[0]["proposed_disclosure"]


def test_an_entry_missing_its_owner_is_rejected(tmp_path):
    data_dir = copy_of_data(tmp_path)
    toml_file = data_dir / "inventory.toml"
    toml_file.write_text(toml_file.read_text().replace('owner = "Financial Crime, Mei Lin Tan"\n', "", 1))
    with pytest.raises(inventory.InventoryError, match="owner"):
        inventory.load(data_dir)


def test_a_missing_prompt_file_is_rejected(tmp_path):
    data_dir = copy_of_data(tmp_path)
    (data_dir / "prompts" / "help-chat-v4.txt").unlink()
    with pytest.raises(inventory.InventoryError, match="does not exist"):
        inventory.load(data_dir)


def test_a_system_listed_twice_is_rejected(tmp_path):
    data_dir = copy_of_data(tmp_path)
    toml_file = data_dir / "inventory.toml"
    text = toml_file.read_text()
    first = text.index("[[system]]")
    second = text.index("[[system]]", first + 1)
    toml_file.write_text(text + "\n" + text[first:second])
    with pytest.raises(inventory.InventoryError, match="listed twice"):
        inventory.load(data_dir)


def test_the_prompt_version_follows_the_date():
    chat = inventory.load()["help-chat"]
    assert chat.version_at("2026-09-01T09:00:00Z").version == "3"
    assert chat.version_at("2026-09-09T09:00:00Z").version == "4"
    assert chat.version_at("2026-08-01T09:00:00Z") is None
