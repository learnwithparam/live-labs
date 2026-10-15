"""Find the passages a question is about, by the words they share, weighted by how rare each word is."""

import math
import re
from collections import Counter

from oncall.ingest import Passage

STOP = set("a an and are as at be by can do does for from how i if in is it its me my of on or the this to what when where which who why with you".split())


def words(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in STOP]


class Index:
    """TF-IDF over the passages. Plain on purpose: this lab is about ingestion and citations, not ranking."""

    def __init__(self, passages: list[Passage]):
        self.passages = passages
        # The heading is indexed with the text: "Poison message" is the words an engineer asks with.
        counts = [Counter(words(f"{passage.section} {passage.text}")) for passage in passages]
        seen = Counter(word for passage_counts in counts for word in passage_counts)
        # A word in every passage (ledger, payments) says nothing; a rare one (kcat, freeze) says a lot.
        self.idf = {word: math.log(len(passages) / passages_with_word) + 1 for word, passages_with_word in seen.items()}
        self.vectors = [self.weigh(passage_counts) for passage_counts in counts]

    def weigh(self, counts: Counter) -> dict[str, float]:
        vector = {word: count * self.idf.get(word, 0.0) for word, count in counts.items()}
        norm = math.sqrt(sum(weight * weight for weight in vector.values())) or 1.0
        return {word: weight / norm for word, weight in vector.items()}

    def search(self, question: str, top_k: int = 4) -> list[Passage]:
        query = self.weigh(Counter(words(question)))
        scores = [sum(query.get(word, 0.0) * weight for word, weight in vector.items()) for vector in self.vectors]
        # Only passages that share a word with the question: an empty list lets the answer say "not covered".
        ranked = sorted(range(len(scores)), key=lambda index: -scores[index])
        return [self.passages[index] for index in ranked[:top_k] if scores[index] > 0]
