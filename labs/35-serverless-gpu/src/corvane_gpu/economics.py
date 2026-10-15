"""Cost per 1,000 items for the served and hosted model, and the batch size where the GPU starts to win.

A serverless GPU bills every second a replica is up: the cold start, the work and the idle window
before it scales to zero. The hosted model bills per token. So the GPU has a fixed cost per wake-up
and a small cost per item, and the hosted model has no fixed cost and a larger one per item.
"""

import math

VOLUMES = [100, 1_000, 10_000, 100_000]


def per_thousand(total_cost: float, items: int) -> float | None:
    return round(total_cost / items * 1000, 4) if items else None


def wake_cost(price_per_second: float, cold_start_seconds: float, busy_seconds: float, idle_seconds: float) -> dict:
    """What one wake-up of the endpoint costs, split the way the bill accrues.

    The cold start and the idle window are paid on every wake, whatever the batch size. Only the
    busy seconds grow with the number of items.
    """
    billed = cold_start_seconds + busy_seconds + idle_seconds
    return {"cold_start_seconds": round(cold_start_seconds, 2), "busy_seconds": round(busy_seconds, 2),
            "idle_seconds": round(idle_seconds, 2), "billed_seconds": round(billed, 2),
            "cost": round(billed * price_per_second, 6)}


def gpu_cost_for(items: int, fixed_seconds: float, seconds_per_item: float, price_per_second: float) -> float:
    return (fixed_seconds + items * seconds_per_item) * price_per_second


def break_even_items(fixed_seconds: float, seconds_per_item: float, price_per_second: float,
                     hosted_cost_per_item: float) -> int | None:
    """The smallest batch per wake-up at which the GPU costs no more than the hosted model.

    Below it, the cold start and idle window cost more than the tokens they save. None means the GPU
    never wins: each item costs more in GPU seconds than the hosted model charges for it.
    """
    saving_per_item = hosted_cost_per_item - seconds_per_item * price_per_second
    if saving_per_item <= 0:
        return None
    return math.ceil(fixed_seconds * price_per_second / saving_per_item)


def compare(gpu_wave: dict, golden_items: int, hosted_total_cost: float, hosted_items: int,
            price_per_second: float) -> dict:
    """Both sides' cost per 1,000 items on the golden set, the break-even batch and a table by volume.

    The served side is priced from measured seconds: the cold start, the busy time of the golden burst
    and the scaledown window. The hosted side is what OpenRouter billed for the same items.
    """
    fixed = gpu_wave["cold_start_seconds"] + gpu_wave["idle_seconds"]
    seconds_per_item = gpu_wave["busy_seconds"] / golden_items if golden_items else 0.0
    hosted_per_item = hosted_total_cost / hosted_items if hosted_items else 0.0
    by_volume = [{"items": volume,
                  "gpu": round(gpu_cost_for(volume, fixed, seconds_per_item, price_per_second), 4),
                  "hosted": round(volume * hosted_per_item, 4)} for volume in VOLUMES]
    return {
        "gpu_per_thousand": per_thousand(gpu_wave["cost"], golden_items),
        "hosted_per_thousand": per_thousand(hosted_total_cost, hosted_items),
        "fixed_seconds_per_wake": round(fixed, 2),
        "gpu_seconds_per_item": round(seconds_per_item, 5),
        "break_even_items": break_even_items(fixed, seconds_per_item, price_per_second, hosted_per_item),
        "by_volume": by_volume,
    }
