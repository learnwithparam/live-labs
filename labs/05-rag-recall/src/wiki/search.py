"""Find the runbook section a question needs, four ways, all on one Qdrant collection.

keyword    BM25 over the words, as a sparse vector. Keeps exact names like RECON-MISMATCH-22.
embedding  a dense vector per passage. Finds a paraphrase that shares no words with the passage.
hybrid     both lists merged by rank (reciprocal rank fusion), so each covers the other's misses.
rerank     the hybrid top 20, reordered by a cross-encoder that reads question and passage together.
"""

from dataclasses import dataclass
from typing import Protocol

from qdrant_client import QdrantClient, models

from wiki.ingest import Passage

WAYS = ("keyword", "embedding", "hybrid", "rerank")
COLLECTION = "runbooks"
# The reranker reads this many hybrid hits. A cross-encoder is slow per pair, so it reorders a short
# list; it can never find a passage the first search did not bring back.
SHORTLIST = 20


class Encoders(Protocol):
    """The three models. The real ones are fastembed's; the tests pass fakes so they need no download."""

    size: int

    def dense(self, texts: list[str], query: bool) -> list[list[float]]: ...
    def sparse(self, texts: list[str], query: bool) -> list[models.SparseVector]: ...
    def rerank(self, question: str, texts: list[str]) -> list[float]: ...


@dataclass
class Hit:
    passage: Passage
    score: float


def text_of(p: Passage) -> str:
    # The heading goes in with the body: engineers ask with the words a heading uses ("poison message").
    return f"{p.section}. {p.text}"


class Search:
    def __init__(self, passages: list[Passage], encoders: Encoders, client: QdrantClient | None = None):
        self.passages, self.encoders = passages, encoders
        # In memory by default, so the lab needs no server. Pass a client for a Qdrant server and nothing
        # else changes: the same collection, vectors and queries.
        self.client = client or QdrantClient(":memory:")
        if self.client.collection_exists(COLLECTION):
            self.client.delete_collection(COLLECTION)
        self.client.create_collection(
            COLLECTION,
            vectors_config={"dense": models.VectorParams(size=encoders.size, distance=models.Distance.COSINE)},
            # BM25 needs to know how rare each word is across all passages; Qdrant keeps that count (IDF).
            sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        texts = [text_of(p) for p in passages]
        dense, sparse = encoders.dense(texts, query=False), encoders.sparse(texts, query=False)
        points = [models.PointStruct(id=i, vector={"dense": d, "bm25": s}) for i, (d, s) in enumerate(zip(dense, sparse))]
        self.client.upsert(COLLECTION, points)

    def find(self, question: str, how: str, k: int = 5) -> list[Hit]:
        if how not in WAYS:
            raise ValueError(f"how must be one of {WAYS}")
        if how == "rerank":
            return self.reranked(question, k)
        dense = self.encoders.dense([question], query=True)[0]
        sparse = self.encoders.sparse([question], query=True)[0]
        if how == "keyword":
            found = self.client.query_points(COLLECTION, query=sparse, using="bm25", limit=k).points
        elif how == "embedding":
            found = self.client.query_points(COLLECTION, query=dense, using="dense", limit=k).points
        else:
            # Each search brings back its own top list, then fusion scores a passage by its rank in each:
            # 1/(60 + rank). Ranks, not raw scores, because BM25 and cosine scores are on different scales.
            fetch = max(k, SHORTLIST)
            prefetch = [models.Prefetch(query=sparse, using="bm25", limit=fetch), models.Prefetch(query=dense, using="dense", limit=fetch)]
            found = self.client.query_points(COLLECTION, prefetch=prefetch, query=models.FusionQuery(fusion=models.Fusion.RRF), limit=k).points
        return [Hit(self.passages[p.id], p.score) for p in found]

    def reranked(self, question: str, k: int) -> list[Hit]:
        shortlist = self.find(question, "hybrid", SHORTLIST)
        # A cross-encoder reads the question and one passage as a single input, so it sees that
        # "us-east-1 payouts" belongs to the payout runbook, which two separate vectors cannot.
        scores = self.encoders.rerank(question, [text_of(h.passage) for h in shortlist])
        ranked = sorted(zip(shortlist, scores), key=lambda pair: -pair[1])
        return [Hit(hit.passage, score) for hit, score in ranked[:k]]
