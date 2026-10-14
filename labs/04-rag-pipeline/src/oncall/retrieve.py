"""Find the passages a question is about, by the words they share, weighted by how rare each word is."""

import math
import re
from collections import Counter

from oncall.ingest import Passage

STOP = set("a an and are as at be by can do does for from how i if in is it its me my of on or the this to what when where which who why with you".split())


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP]


class Index:
    """TF-IDF over the passages. Plain on purpose: this lab is about ingestion and citations, not ranking."""

    def __init__(self, passages: list[Passage]):
        self.passages = passages
        counts = [Counter(words(f"{p.section} {p.text}")) for p in passages]
        seen = Counter(w for c in counts for w in c)
        self.idf = {w: math.log(len(passages) / n) + 1 for w, n in seen.items()}
        self.vectors = [self.weigh(c) for c in counts]

    def weigh(self, counts: Counter) -> dict[str, float]:
        vector = {w: n * self.idf.get(w, 0.0) for w, n in counts.items()}
        norm = math.sqrt(sum(v * v for v in vector.values())) or 1.0
        return {w: v / norm for w, v in vector.items()}

    def search(self, question: str, k: int = 4) -> list[Passage]:
        query = self.weigh(Counter(words(question)))
        scores = [sum(query.get(w, 0.0) * v for w, v in vector.items()) for vector in self.vectors]
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])
        return [self.passages[i] for i in ranked[:k] if scores[i] > 0]
