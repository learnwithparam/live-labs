"""The whole GPU run rehearsed with the stand-in server as a real child process: the key, two wake-ups
from zero, the scale back to zero, the bill per wake-up and the time guard."""

import time

from corvane_gpu.golden import EXAMPLES, golden_set, read_jsonl
from corvane_gpu.gpu_run import stand_in_command
from corvane_gpu.replica import Plan, run_waves

# Its own port in the lab's range, so the test never meets a console or rehearsal left running.
PORT = 8355
ITEMS = golden_set()[:12]


def plan(out_dir, deadline: float, waves=None) -> Plan:
    return Plan(server_command=stand_in_command(PORT, startup_seconds=0.5), port=PORT,
                waves=waves or [("golden set", ITEMS), ("second wake", ITEMS[:4])], examples=read_jsonl(EXAMPLES),
                served_name="stand-in", out_dir=out_dir, concurrency=4, request_timeout=2.0, cold_start_timeout=10,
                idle_seconds=0.3, deadline=deadline, price_per_second=0.0002, wave_minimum_seconds=3)


def test_each_wave_starts_from_zero_and_ends_at_zero(tmp_path):
    results = run_waves(plan(tmp_path, time.time() + 60))
    assert results["auth"] == {"no_key": 401, "wrong_key": 401, "right_key": 200}
    assert [event["replicas"] for event in results["timeline"]] == [0, 1, 1, 1, 0, 1, 1, 1, 0]
    for wave in results["waves"]:
        billing = wave["billing"]
        assert wave["started"] and wave["cold_start_seconds"] >= 0.5
        assert len(wave["outcomes"]) == wave["items"]
        parts = billing["cold_start_seconds"] + billing["busy_seconds"] + billing["idle_seconds"]
        assert abs(billing["billed_seconds"] - parts) <= 0.02
    assert (tmp_path / "results.json").exists()


def test_the_guard_skips_a_wave_it_cannot_finish(tmp_path):
    results = run_waves(plan(tmp_path, time.time() + 1))
    assert results["guard_hit"] is True
    assert results["waves"] == [{"name": "golden set", "items": len(ITEMS), "started": False,
                                 "reason": "not started: the time guard left too little time for a cold start"}]
    assert results["timeline"][-1]["replicas"] == 0


def test_a_server_that_never_comes_up_fails_every_item(tmp_path):
    broken = plan(tmp_path, time.time() + 60, waves=[("golden set", ITEMS)])
    broken.server_command = ["python3", "-c", "raise SystemExit('libcudart.so.13: cannot open shared object file')"]
    results = run_waves(broken)
    wave = results["waves"][0]
    assert len(wave["outcomes"]) == len(ITEMS)
    assert all(outcome["status"] == "timed_out" and "cold start" in outcome["error"] for outcome in wave["outcomes"])


def test_a_broken_install_still_writes_every_item_as_failed(tmp_path, monkeypatch):
    import corvane_gpu.gpu_run as gpu_run
    from corvane_gpu.golden import DATA

    def install_that_times_out(deadline: float) -> dict:
        raise AssertionError("vllm did not install: timed out after 170 s")

    monkeypatch.setattr(gpu_run, "check_gpu", lambda: {"gpu": "Tesla T4"})
    monkeypatch.setattr(gpu_run, "install_vllm", install_that_times_out)
    results = gpu_run.run_on_gpu(DATA, tmp_path, time.time())
    written = (tmp_path / "results.json").read_text()
    assert "timed out after 170 s" in results["error"] and "timed out after 170 s" in written
    outcomes = results["waves"][0]["outcomes"]
    assert len(outcomes) == len(golden_set()) and all(outcome["status"] == "timed_out" for outcome in outcomes)
