"""One report from the GPU run, the hosted run and the reviewer's decisions, for the CLI, the page and the proof.

Costs are recomputed here from the measured seconds and the sourced price, so a price correction
never needs a new GPU run. The run's own numbers stay as measured.
"""

from corvane_gpu.config import PRICE_SOURCE, REPLICA_PRICE_PER_SECOND
from corvane_gpu.economics import compare, wake_cost
from corvane_gpu.golden import CATEGORIES
from corvane_gpu.latency import accuracy, summarise


def queue_reason(outcome: dict) -> str:
    """Why an item needs a person. Each reason is one the platform team can act on."""
    if outcome["status"] == "timed_out":
        return f"timed out: {outcome['error']}"
    if outcome["status"] != "ok":
        return f"request failed: {outcome['error']}"
    return f"answer is not one category: {outcome['answer'][:80]!r}"


def review_queue(outcomes: list[dict], golden: dict[str, dict], decided: dict[str, dict]) -> list[dict]:
    """Every golden-set item without a valid category, with the reviewer's decision when there is one.

    A timeout is in the queue like any wrong-format answer: fail closed means the item is not done
    until someone has decided it.
    """
    queue = []
    for outcome in outcomes:
        if outcome["label"] in CATEGORIES:
            continue
        item = golden[outcome["id"]]
        queue.append({"id": outcome["id"], "descriptor": item["descriptor"], "amount": item["amount"],
                      "status": outcome["status"], "reason": queue_reason(outcome),
                      "decision": decided.get(outcome["id"])})
    return queue


def wave_view(wave: dict, truth: dict[str, str]) -> dict:
    view = {key: wave.get(key) for key in ("name", "items", "started", "reason", "cold_start_seconds", "smoke")}
    if wave.get("outcomes") is not None:
        view["latency"] = summarise(wave["outcomes"])
        view["accuracy"] = accuracy(wave["outcomes"], truth)
    if wave.get("billing"):
        billing = wave["billing"]
        view["billing"] = wake_cost(REPLICA_PRICE_PER_SECOND, billing["cold_start_seconds"], billing["busy_seconds"],
                                    billing["idle_seconds"])
    return view


def build_report(gpu_run: dict, hosted_run: dict | None, golden_items: list[dict], decided: dict[str, dict]) -> dict:
    golden = {item["id"]: item for item in golden_items}
    truth = {item["id"]: item["label"] for item in golden_items}
    waves = [wave_view(wave, truth) for wave in gpu_run["waves"]]
    golden_wave = next(wave for wave in gpu_run["waves"] if wave["name"] == "golden set")
    report = {
        "about": gpu_run["about"], "auth": gpu_run["auth"], "timeline": gpu_run["timeline"],
        "settings": gpu_run["settings"], "guard_hit": gpu_run["guard_hit"],
        "kernel_seconds": gpu_run.get("kernel_seconds"), "waves": waves,
        "price": {"per_second": REPLICA_PRICE_PER_SECOND, "source": PRICE_SOURCE},
        "queue": review_queue(golden_wave.get("outcomes") or [], golden, decided),
        "hosted": None, "economics": None,
    }
    golden_view = next(view for view in waves if view["name"] == "golden set")
    if hosted_run:
        hosted_outcomes = hosted_run["outcomes"]
        report["hosted"] = {"model": hosted_run["model"], "latency": summarise(hosted_outcomes),
                            "accuracy": accuracy(hosted_outcomes, truth), "total_cost": hosted_run["total_cost"],
                            "recorded": hosted_run.get("recorded", False)}
    if hosted_run and golden_view.get("billing"):
        report["economics"] = compare(golden_view["billing"], len(golden_items), hosted_run["total_cost"],
                                      len(hosted_outcomes), REPLICA_PRICE_PER_SECOND)
    return report
