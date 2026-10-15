"""Find the chunks that answer a question, across the drive, the wiki and the ticket tool.

Retrieval here is plain BM25 over paragraphs, because this lab is about what happens after retrieval:
any retriever, keyword or vector, returns chunks from documents the asker may not open. Each chunk keeps
the id of the document it came from, and that id is what OpenFGA is asked about.
"""

import math
import re
from collections import Counter

STOPWORDS = set("a an and are as at be by did do does for from how i in is it of on or our the this to was we what "
                "when which who why with".split())


def words(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in STOPWORDS]


def chunks_of(documents: list[dict]) -> list[dict]:
    """One chunk per paragraph, with the title in front so a short paragraph still says what it is about."""
    return [{"id": f"{document['id']}#{position}", "document": document["id"], "title": document["title"],
             "source": document["source"], "place": document["place"], "text": paragraph}
            for document in documents for position, paragraph in enumerate(document["text"].split("\n\n"))]


class Index:
    """BM25 with the usual k1 and b (here `length_weight`); the scores only need to rank, not to mean anything alone."""

    def __init__(self, chunks: list[dict], k1: float = 1.4, length_weight: float = 0.75):
        self.chunks, self.k1, self.length_weight = chunks, k1, length_weight
        self.terms = [Counter(words(chunk["title"] + " " + chunk["text"])) for chunk in chunks]
        self.average = sum(sum(terms.values()) for terms in self.terms) / max(len(chunks), 1)
        frequency = Counter(word for terms in self.terms for word in terms)
        self.idf = {word: math.log(1 + (len(chunks) - count + 0.5) / (count + 0.5)) for word, count in frequency.items()}

    def score(self, query: list[str], position: int) -> float:
        terms, length = self.terms[position], sum(self.terms[position].values())
        return sum(self.idf.get(word, 0.0) * terms[word] * (self.k1 + 1)
                   / (terms[word] + self.k1 * (1 - self.length_weight + self.length_weight * length / self.average)) for word in query)

    def search(self, question: str, limit: int, only: set[str] | None = None) -> list[dict]:
        """Top chunks for the question. `only` limits the search to these documents: the pre-filter."""
        query = words(question)
        scored = [(self.score(query, position), position) for position, chunk in enumerate(self.chunks)
                  if only is None or chunk["document"] in only]
        ranked = sorted((pair for pair in scored if pair[0] > 0), key=lambda pair: -pair[0])
        return [{**self.chunks[position], "score": round(score, 2)} for score, position in ranked[:limit]]
