"""Every test reads and writes the labeller's files and saved runs under a scratch folder, never the lab's own."""

import pytest

import brickhaven_evals.config as settings
import brickhaven_evals.golden as golden_module


@pytest.fixture(autouse=True)
def scratch_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(golden_module, "VERIFIED_BY_LABELLER", tmp_path / "verified.jsonl")
    monkeypatch.setattr(settings, "RUNS", tmp_path / "runs")
