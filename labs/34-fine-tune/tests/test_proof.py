"""The lab's promise, on real models: the adapter trained on Kaggle from the reviewers' labels makes Qwen3
0.6B far better than its untuned self, but alone it is held. Put in front of the large model, labelling
only what it is sure of, it matches the large model for less, and ships. The truth is the labels on
held-out items no model trained or was prompted on.

It scores the adapter `uv run tune train` downloaded, so it spends no GPU time of its own.
"""

import json
import os
from pathlib import Path

import pytest

from corvane.cli import RELEASE, baseline, evaluate, export_labels, run_cascade
from corvane.config import load_dotenv
from corvane.release import decide_release
from corvane.train import ADAPTER_DIR, TRAIN_REPORT, dataset_version

load_dotenv()
README = Path("README.md")


@pytest.mark.proof
@pytest.mark.skipif(not os.environ.get("LLM_API_KEY"), reason="needs LLM_API_KEY for the large model")
def test_the_tuned_model_alone_is_held_and_the_cascade_ships():
    assert (ADAPTER_DIR / "adapters.safetensors").exists(), "run `uv run tune train` first: it trains on Kaggle"
    assert export_labels() == 0
    trained = json.loads(TRAIN_REPORT.read_text())
    # The adapter must come from the labels exported now, or the test split could have leaked into it.
    assert trained["dataset_version"] == dataset_version()
    assert trained["gpu_minutes"] < 15 and not trained["stopped_early"]
    assert baseline() == 0
    assert evaluate() == 0
    scores = json.loads(Path("out/scores.json").read_text())
    large, untuned, tuned = scores["large"], scores["untuned"], scores["tuned"]
    assert tuned["accuracy"] > untuned["accuracy"] and tuned["cost_per_item"] < large["cost_per_item"]
    assert decide_release(tuned, large)["decision"] == "hold"
    assert run_cascade() == 0
    release = json.loads(RELEASE.read_text())
    assert release["decision"] == "ship", release["reasons"]
    card = json.loads((ADAPTER_DIR / "card.json").read_text())
    assert card["decision"] == "ship" and card["dataset_version"] == trained["dataset_version"]
    # The README's figures that depend only on the adapter come from this run, so a rerun that moves one fails
    # here. The large model's answers can shift between calls, so its figures are stated as one run's.
    readme = README.read_text()
    for figure in (f"{release['kept']} of {release['total']} labelled by the small model",
                   f"{release['routed']} sent to the large model", f"threshold {release['threshold']:.0%}",
                   f"{tuned['accuracy']:.1%}", f"{untuned['accuracy']:.1%}",
                   f"{trained['gpu_minutes']} GPU minutes", f"{trained['steps']} steps"):
        assert figure in readme, figure
