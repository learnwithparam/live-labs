import sys
from pathlib import Path

import pytest

# The shared fakes live beside the tests.
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def fresh_state(tmp_path, monkeypatch):
    """Every test gets its own ledger, keys and decisions, so no test sees another's spend."""
    monkeypatch.setenv("FERNWAY_STATE", str(tmp_path / "out"))
    return tmp_path / "out"
