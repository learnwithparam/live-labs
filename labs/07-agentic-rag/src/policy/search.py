"""Keyword search over the policy sections, the one tool both pipelines share.

Plain RAG and the agent search the same index with the same k, so any difference in their answers
comes from what they search for and how often, not from a better retriever. BM25 is enough: on-call
engineers and the runbooks share words like "ledger-writer" and "FLUSHALL".
"""

import bm25s
import Stemmer

from policy.ingest import Passage

TOP_K = 3
# "deploys" in a section must match "deploy" in a search; without a stemmer they are different words.
STEM = Stemmer.Stemmer("english")


class Policies:
    def __init__(self, passages: list[Passage]):
        self.passages = passages
        self.by_id = {passage.id: passage for passage in passages}
        # The document title and heading go into the index too: "Freeze window" is the words a person searches.
        corpus = [f"{passage.doc} {passage.section} {passage.text}" for passage in passages]
        self.index = bm25s.BM25()
        self.index.index(bm25s.tokenize(corpus, stopwords="en", stemmer=STEM, show_progress=False), show_progress=False)

    def search(self, query: str, top_k: int = TOP_K) -> list[Passage]:
        tokens = bm25s.tokenize([query], stopwords="en", stemmer=STEM, show_progress=False)
        found, scores = self.index.retrieve(tokens, k=min(top_k, len(self.passages)), show_progress=False)
        # A score of 0 means no shared word: that passage would only invite a guess.
        return [self.passages[index] for index, score in zip(found[0], scores[0]) if score > 0]

    def get(self, ids: list[str]) -> list[Passage]:
        return [self.by_id[passage_id] for passage_id in ids if passage_id in self.by_id]


def listing(passages: list[Passage]) -> str:
    # Each passage carries its id in brackets, so the model can cite exactly the one it used.
    return "\n\n".join(f"[{passage.id}] {passage.doc}, {passage.section}\n{passage.text}" for passage in passages)
