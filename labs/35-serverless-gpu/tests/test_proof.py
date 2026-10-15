"""The lab's promise on real runs: vLLM served the golden set on a real GPU that started from zero and went
back to zero, and its cost per 1,000 items and p95 are set against a live run of the hosted model on the
same items. The truth is the reviewers' labels in data/golden.jsonl."""

import json
import os

import pytest

from corvane_gpu.config import GPU_RUN, REPLICA_PRICE_PER_SECOND, llm_settings, load_dotenv
from corvane_gpu.economics import gpu_cost_for
from corvane_gpu.golden import CATEGORIES, EXAMPLES, golden_set, read_jsonl
from corvane_gpu.hosted import run_hosted
from corvane_gpu.report import build_report


@pytest.fixture(scope="module")
def hosted_live():
    load_dotenv()
    if not os.environ.get("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY is not set")
    base_url, key, model = llm_settings()
    return run_hosted(golden_set(), read_jsonl(EXAMPLES), base_url, key, model)


@pytest.mark.proof
def test_the_hosted_model_answers_the_golden_set_live(hosted_live):
    golden = golden_set()
    report = build_report({"about": {}, "auth": {}, "timeline": [], "settings": {}, "guard_hit": False,
                           "waves": [{"name": "golden set", "outcomes": []}]}, hosted_live, golden, {})
    hosted = report["hosted"]
    assert [outcome["id"] for outcome in hosted_live["outcomes"]] == [item["id"] for item in golden]
    assert hosted["latency"]["failed"] == 0
    assert hosted["latency"]["p95_seconds"] > 0
    assert hosted["accuracy"]["accuracy"] >= 0.9
    # What OpenRouter billed, not a price list: every answered call carries its own cost.
    assert all(outcome["cost"] is not None for outcome in hosted_live["outcomes"])
    assert hosted_live["total_cost"] > 0


@pytest.mark.proof
def test_the_gpu_run_scaled_from_zero_and_back_and_is_priced_against_hosted(hosted_live):
    assert GPU_RUN.exists(), f"No real GPU run at {GPU_RUN}. Run kaggle/run_on_kaggle.sh first."
    gpu_run = json.loads(GPU_RUN.read_text())
    golden = golden_set()
    report = build_report(gpu_run, hosted_live, golden, {})
    about = report["about"]
    # A real GPU, never the stand-in, and a run that set itself up.
    assert "error" not in gpu_run, gpu_run.get("error")
    assert about["where"] == "Kaggle notebook" and "T4" in about["gpu"]
    assert report["auth"] == {"no_key": 401, "wrong_key": 401, "right_key": 200}
    replicas = [event["replicas"] for event in report["timeline"]]
    assert replicas[0] == 0 and replicas[-1] == 0
    wake_ups = sum(1 for before, after in zip(replicas, replicas[1:]) if before == 0 and after == 1)
    assert wake_ups == 2, "both waves must start from zero replicas"
    golden_wave = next(wave for wave in report["waves"] if wave["name"] == "golden set")
    assert golden_wave["started"] and golden_wave["items"] == len(golden)
    assert golden_wave["latency"]["items"] == len(golden)
    assert golden_wave["latency"]["p95_seconds"] is not None
    billing = golden_wave["billing"]
    assert billing["cold_start_seconds"] > 0 and billing["idle_seconds"] > 0
    # Fail closed: every item without one valid category waits in the queue for a person.
    raw_outcomes = next(wave for wave in gpu_run["waves"] if wave["name"] == "golden set")["outcomes"]
    without_category = {outcome["id"] for outcome in raw_outcomes if outcome["label"] not in CATEGORIES}
    assert {item["id"] for item in report["queue"]} == without_category
    economics = report["economics"]
    assert economics["gpu_per_thousand"] > 0 and economics["hosted_per_thousand"] > 0
    break_even = economics["break_even_items"]
    hosted_per_item = hosted_live["total_cost"] / len(hosted_live["outcomes"])

    def cost(items: int) -> float:
        return gpu_cost_for(items, economics["fixed_seconds_per_wake"], economics["gpu_seconds_per_item"],
                            REPLICA_PRICE_PER_SECOND)

    # The honest finding either way: at ten items a wake-up, the hosted model is cheaper.
    assert cost(10) > 10 * hosted_per_item
    if break_even is not None:
        assert cost(break_even) <= break_even * hosted_per_item * 1.001
        assert cost(break_even - 1) > (break_even - 1) * hosted_per_item
