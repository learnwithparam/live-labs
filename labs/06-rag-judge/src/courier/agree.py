"""Agreement between a judge and people, on the same replies.

Raw agreement flatters a judge when most replies pass: a judge that passes everything agrees on every
good reply. Cohen's kappa subtracts the agreement two raters would reach by chance from their own
label rates, so 0 means no better than chance and 1 means they always agree.
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Labelled:
    id: str
    courier: str
    question: str
    answer: str
    cited: list[str]
    human: str  # the support agent's verdict, "pass" or "fail"
    kind: str  # supported, unsupported, unsafe or off-question
    reason: str


def load_labelled(path: Path) -> list[Labelled]:
    return [Labelled(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def kappa(human: list[str], judge: list[str]) -> float:
    n = len(human)
    observed = sum(h == j for h, j in zip(human, judge)) / n
    # Chance agreement: both say pass by luck, plus both say fail by luck, from each one's own rates.
    chance = sum((human.count(v) / n) * (judge.count(v) / n) for v in ("pass", "fail"))
    return 1.0 if chance == 1 else (observed - chance) / (1 - chance)


def agreement(rows: list[Labelled], verdicts: list[str]) -> dict:
    human = [r.human for r in rows]
    # Rows are the person's verdict, columns the judge's: the off-diagonal cells are the ones to read.
    matrix = {h: {j: sum(1 for a, b in zip(human, verdicts) if (a, b) == (h, j)) for j in ("pass", "fail")} for h in ("pass", "fail")}
    return {
        "agree": sum(h == j for h, j in zip(human, verdicts)),
        "total": len(rows),
        "kappa": round(kappa(human, verdicts), 2),
        "matrix": matrix,
        "disagree": [r.id for r, v in zip(rows, verdicts) if v != r.human],
    }
