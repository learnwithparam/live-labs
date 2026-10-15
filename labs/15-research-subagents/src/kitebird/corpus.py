"""Kitebird's source library and the two tools every research agent gets: `search` and `read`.

A live web search returns different pages every day, so two runs could not be compared and the proof
could not be repeated. A local library of dated sources keeps the hard parts of research: some are off
topic, some are stale, and some disagree with each other. Only the model's choices vary between runs.
"""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
WORD = re.compile(r"[a-z0-9]+")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
# Words that match every source say nothing about which source is relevant, so ranking ignores them.
STOP = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or",
        "the", "to", "what", "who", "with", "how", "does", "do", "much", "many", "about"}
RESULTS = 5
# BM25's usual settings: how fast repeats of a word stop counting, and how much long sources are discounted.
SATURATION, LENGTH_WEIGHT = 1.2, 0.75


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    publisher: str
    date: str
    text: str

    def sentences(self) -> list[str]:
        return SENTENCE.split(self.text)


def words(text: str) -> list[str]:
    return [word for word in WORD.findall(text.lower()) if word not in STOP]


@lru_cache(maxsize=1)
def load_sources(folder: Path = DATA / "sources") -> dict[str, Source]:
    """Each file is a few `key: value` lines, a blank line, then the text as the publisher wrote it."""
    sources = {}
    for path in sorted(folder.glob("S*.md")):
        head, _, body = path.read_text().partition("\n\n")
        fields = dict(line.split(": ", 1) for line in head.splitlines())
        # One space between words, so a quote copied across a line break still matches character for character.
        sources[path.stem] = Source(path.stem, fields["title"], fields["publisher"], fields["date"], " ".join(body.split()))
    return sources


def load_brief(path: Path = DATA / "brief.json") -> dict:
    return json.loads(path.read_text())


class Library:
    """BM25 ranking over title and text: the keyword search most production retrievers still start from."""

    def __init__(self, sources: dict[str, Source] | None = None):
        self.sources = sources or load_sources()
        self.counts = {source_id: Counter(words(f"{source.title} {source.text}")) for source_id, source in self.sources.items()}
        self.average_length = sum(sum(counts.values()) for counts in self.counts.values()) / len(self.counts)
        self.holding = Counter(word for counts in self.counts.values() for word in counts)

    def score(self, query_words: list[str], source_id: str) -> float:
        counts, length, total = self.counts[source_id], sum(self.counts[source_id].values()), len(self.counts)
        score = 0.0
        for word in query_words:
            if word not in counts:
                continue
            # A word found in few sources is strong evidence; one found in most sources is weak.
            rarity = math.log(1 + (total - self.holding[word] + 0.5) / (self.holding[word] + 0.5))
            damped = counts[word] * (SATURATION + 1) / (counts[word] + SATURATION * (1 - LENGTH_WEIGHT + LENGTH_WEIGHT * length / self.average_length))
            score += rarity * damped
        return score

    def search(self, query: str, limit: int = RESULTS) -> list[dict]:
        query_words = words(query)
        ranked = sorted(((self.score(query_words, source_id), source_id) for source_id in self.sources), reverse=True)
        hits = []
        for score, source_id in ranked[:limit]:
            if score <= 0:
                break
            source = self.sources[source_id]
            # The snippet is the two sentences that share most words with the query, so the agent can
            # judge relevance without reading every source in full.
            best = sorted(source.sentences(), key=lambda sentence: -len(set(words(sentence)) & set(query_words)))[:2]
            snippet = " ".join(sentence for sentence in source.sentences() if sentence in best)
            hits.append({"source": source_id, "title": source.title, "publisher": source.publisher,
                         "date": source.date, "snippet": snippet, "score": round(score, 2)})
        return hits

    def search_text(self, query: str) -> str:
        hits = self.search(query)
        if not hits:
            return f"No source matches {query!r}. Try other words."
        return "\n\n".join(f"{hit['source']} | {hit['publisher']} | {hit['date']}\n{hit['title']}\n{hit['snippet']}" for hit in hits)

    def read(self, source_id: str) -> str:
        source = self.sources.get(source_id.strip().upper())
        if not source:
            return f"No source {source_id!r}. Use an id from search results, like S04."
        return f"{source.id} | {source.publisher} | {source.date}\n{source.title}\n\n{source.text}"
