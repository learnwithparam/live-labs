"""The numbers the risk team watches, all graded on fraud found later, never on what a model said.

Recall at a fixed review rate is the product metric: the team reviews a set share of refunds, so the
better scorer is the one that puts more of the later fraud inside that share. Brier score and
reliability bins answer a second question: when the model says 0.3, is it fraud 3 times in 10?
"""

import numpy


def caught_at_rate(scores: numpy.ndarray, frauds: numpy.ndarray, rate: float) -> dict:
    """Send the riskiest `rate` of refunds to review and count the later fraud inside them.

    The order is stable, so two refunds with the same score keep their file order and a scorer that
    gives many ties (a rule, or a model that answers in round numbers) gets no lucky break.
    """
    reviewed = max(1, round(len(scores) * rate))
    riskiest = numpy.argsort(-scores, kind="stable")[:reviewed]
    caught = int(frauds[riskiest].sum())
    return {"reviewed": reviewed, "caught": caught, "frauds": int(frauds.sum()),
            "recall": caught / max(1, int(frauds.sum())), "precision": caught / reviewed}


def curve(scores: numpy.ndarray, frauds: numpy.ndarray, rates: list[float]) -> list[dict]:
    """Fraud caught at each review rate, for the chart under the page's slider."""
    return [{"rate": rate, **caught_at_rate(scores, frauds, rate)} for rate in rates]


def brier(chances: numpy.ndarray, frauds: numpy.ndarray) -> float:
    """Mean squared gap between the stated chance and what happened. Lower is better; a scorer that
    always says the base rate scores base_rate * (1 - base_rate)."""
    return float(numpy.mean((chances - frauds) ** 2))


def reliability(chances: numpy.ndarray, frauds: numpy.ndarray, bins: int = 10) -> dict:
    """Group refunds by stated chance and compare each group's average chance with its fraud rate.

    The weighted gap is the expected calibration error: how far off the stated chance is, on average,
    for a refund picked at random. Empty bins are left out rather than shown as zero.
    """
    edges = numpy.linspace(0, 1, bins + 1)
    which = numpy.clip(numpy.digitize(chances, edges[1:-1]), 0, bins - 1)
    rows, gap = [], 0.0
    for number in range(bins):
        inside = which == number
        if not inside.any():
            continue
        said, happened = float(chances[inside].mean()), float(frauds[inside].mean())
        rows.append({"from": float(edges[number]), "to": float(edges[number + 1]), "refunds": int(inside.sum()),
                     "said": said, "happened": happened})
        gap += inside.sum() * abs(said - happened)
    return {"bins": rows, "error": float(gap / len(chances))}


def precision_above(chances: numpy.ndarray, frauds: numpy.ndarray, line: float) -> dict:
    """How often a refund above `line` really was fraud: the hold line's promise, checked."""
    above = chances >= line
    return {"held": int(above.sum()), "fraud": int(frauds[above].sum()),
            "precision": float(frauds[above].mean()) if above.any() else 0.0}
