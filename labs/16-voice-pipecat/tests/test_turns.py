import os
from types import SimpleNamespace

from lucca.bot import split_turn
from lucca.review import percentile, score
from lucca.till import Menu


def part(key: str, seconds: float) -> SimpleNamespace:
    return SimpleNamespace(key=key, duration_secs=seconds)


def test_pipecats_named_parts_of_a_turn_fold_into_hear_think_and_speak():
    breakdown = SimpleNamespace(total_secs=1.6, contributions=[
        part("transcription", 0.2), part("turn_detection", 0.1), part("llm_inference", 0.8),
        part("llm_tool_call", 0.2), part("speech_synthesis", 0.3)])
    turn = split_turn(breakdown)
    assert (turn["latency_ms"], turn["hear_ms"], turn["think_ms"], turn["speak_ms"]) == (1600, 300, 1000, 300)
    assert [entry["key"] for entry in turn["parts"]][-1] == "speech_synthesis"


def test_a_part_pipecat_adds_later_counts_as_speak_so_the_three_still_add_up():
    turn = split_turn(SimpleNamespace(total_secs=0.5, contributions=[part("a_new_stage", 0.5)]))
    assert turn["speak_ms"] == 500 and turn["hear_ms"] == turn["think_ms"] == 0


def test_percentiles_use_the_nearest_rank_so_p95_of_a_small_sample_is_its_slowest_turn():
    latencies = [900, 1100, 1300, 1500, 1700, 1900, 2100, 4000]
    assert percentile(latencies, 0.5) == 1500
    assert percentile(latencies, 0.95) == 4000
    assert percentile([], 0.5) is None


def test_a_turn_with_no_split_counts_in_the_totals_but_not_in_the_parts():
    timed = {"latency_ms": 1000, "hear_ms": 200, "think_ms": 600, "speak_ms": 200, "parts": []}
    after_cut_in = {"latency_ms": 3000, "hear_ms": None, "think_ms": None, "speak_ms": None, "parts": []}
    rows = [{"staff_expected": False, "ok": True, "lines": 2, "cost": 0.001, "turns": [timed, after_cut_in]}]
    metric = score(rows)
    assert (metric["timed_turns"], metric["p50_ms"], metric["p95_ms"]) == (2, 1000, 3000)
    assert metric["p50_parts_ms"] == {"hear": 200, "think": 600, "speak": 200}


def test_speech_to_text_is_primed_with_every_menu_item_the_till_knows():
    hint = Menu().hint()
    assert hint.startswith("Lucca Brick Oven: ")
    assert all(item["name"] in hint for item in Menu().data["items"])


def test_a_key_set_in_the_shell_wins_over_the_key_file_when_pipecat_starts_the_bot(monkeypatch):
    import sys

    import dotenv

    from lucca.bot import runner_main

    # Pipecat's runner loads the key file with override=True as it is imported; this stands in for that.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda **kwargs: os.environ.update(LLM_API_KEY="from-file"))
    monkeypatch.delitem(sys.modules, "pipecat.runner.run", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "from-shell")
    runner_main()
    assert os.environ["LLM_API_KEY"] == "from-shell"
