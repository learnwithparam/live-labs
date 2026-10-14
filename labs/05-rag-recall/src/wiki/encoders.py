"""The real models, from fastembed: small ONNX models that run on a laptop CPU and need no key.

The first run downloads them (about 150 MB) into the fastembed cache; after that they load in seconds.
"""

import os

from qdrant_client import models

# The defaults this lab was measured on. Swap one through the environment and rerun `wiki eval`
# to see what a bigger embedding model or another reranker buys.
DENSE = "BAAI/bge-small-en-v1.5"
SPARSE = "Qdrant/bm25"
RERANK = "Xenova/ms-marco-MiniLM-L-6-v2"


class FastEmbed:
    def __init__(self):
        # Imported here so the keyless tests, which pass fake encoders, never load onnxruntime.
        from fastembed import SparseTextEmbedding, TextEmbedding
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.dense_model = TextEmbedding(os.environ.get("DENSE_MODEL") or DENSE)
        self.sparse_model = SparseTextEmbedding(os.environ.get("SPARSE_MODEL") or SPARSE)
        self.reranker = TextCrossEncoder(os.environ.get("RERANK_MODEL") or RERANK)
        self.size = len(next(iter(self.dense_model.embed(["size"]))))

    def dense(self, texts: list[str], query: bool) -> list[list[float]]:
        # bge models embed a question differently from a passage; query_embed adds the instruction for it.
        vectors = self.dense_model.query_embed(texts) if query else self.dense_model.embed(texts)
        return [v.tolist() for v in vectors]

    def sparse(self, texts: list[str], query: bool) -> list[models.SparseVector]:
        vectors = self.sparse_model.query_embed(texts) if query else self.sparse_model.embed(texts)
        return [models.SparseVector(indices=v.indices.tolist(), values=v.values.tolist()) for v in vectors]

    def rerank(self, question: str, texts: list[str]) -> list[float]:
        return [float(s) for s in self.reranker.rerank(question, texts)]
