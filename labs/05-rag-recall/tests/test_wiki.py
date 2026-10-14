"""Tests that need no download: ingestion, the labels, the recall maths, the search ways, the page and env.example."""

import json
import re
import threading
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import pytest
from qdrant_client import models

from wiki import encoders
from wiki.ingest import ingest
from wiki.score import KS, Label, evaluate, load_golden, rank_of, recall
from wiki.search import SHORTLIST, WAYS, Search
from wiki.serve import Room, serve

LAB = Path(__file__).parent.parent
DOCS, GOLDEN = LAB / "data/docs", LAB / "data/golden.jsonl"
PASSAGES = ingest(DOCS)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9-]+", text.lower())


class Fake:
    """Deterministic stand-ins: hashed bag of words for the dense vector, one index per word for BM25,
    shared words for the reranker. Enough to exercise every query path on a real in-memory Qdrant."""

    size = 512

    def dense(self, texts, query):
        out = []
        for text in texts:
            v = [0.0] * self.size
            for w in words(text):
                v[zlib.crc32(w.encode()) % self.size] += 1.0
            out.append(v if any(v) else [1.0] + [0.0] * (self.size - 1))
        return out

    def sparse(self, texts, query):
        out = []
        for text in texts:
            ids = sorted({zlib.crc32(w.encode()) for w in words(text)}) or [0]
            out.append(models.SparseVector(indices=ids, values=[1.0] * len(ids)))
        return out

    def rerank(self, question, texts):
        asked = set(words(question))
        return [float(len(asked & set(words(t)))) for t in texts]


@pytest.fixture(scope="module")
def search():
    return Search(PASSAGES, Fake())


def test_ingest_gives_one_passage_per_section_with_unique_ids():
    assert len(PASSAGES) == 64
    assert len({p.id for p in PASSAGES}) == 64
    assert len({p.doc for p in PASSAGES}) == 14


def test_every_label_points_at_a_real_section():
    ids = {p.id for p in PASSAGES}
    golden = load_golden(GOLDEN)
    assert len(golden) == 40
    assert [g.passage for g in golden if g.passage not in ids] == []


def test_recall_counts_ranks_at_or_above_k():
    ranks = [1, 3, None, 6, 2]
    assert [recall(ranks, k) for k in KS] == [1, 3, 3, 4]


@pytest.mark.parametrize("how", WAYS)
def test_every_way_returns_k_hits_and_finds_an_exact_identifier(search, how):
    hits = search.find("RECON-MISMATCH-22: the ledger has a payment the bank file does not", how, 5)
    # BM25 returns only sections that share a word, so it can return fewer than k.
    assert 1 <= len(hits) <= 5
    assert any(h.passage.id == "reconciliation-job#recon-mismatch-22" for h in hits)


def test_rerank_only_reorders_the_hybrid_shortlist(search):
    question = "consumer stuck on one bad event"
    shortlist = {h.passage.id for h in search.find(question, "hybrid", SHORTLIST)}
    assert {h.passage.id for h in search.find(question, "rerank", 10)} <= shortlist


def test_an_unknown_way_is_refused(search):
    with pytest.raises(ValueError):
        search.find("anything", "magic")


def test_evaluate_lists_the_questions_the_best_way_still_misses(search):
    labels = [Label("RECON-MISMATCH-22: the ledger has a payment the bank file does not", "reconciliation-job#recon-mismatch-22"), Label("zzz", "access-and-secrets#secret-rotation")]
    result = evaluate(search, labels)
    assert result["total"] == 2 and set(result["recall"]) == set(WAYS)
    assert result["rows"][0]["ranks"]["rerank"] == rank_of(search, labels[0], "rerank") <= 5
    assert result["person"] == ["zzz"]


def start(room):
    server = serve(room, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def call(base, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{base}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST" if data else "GET")
    return json.loads(urllib.request.urlopen(request).read())


def test_the_page_searches_reports_quality_and_records_a_decision(search, tmp_path):
    golden = load_golden(GOLDEN)[:3]
    server, base = start(Room(search, golden, tmp_path / "review.jsonl"))
    try:
        room = call(base, "/api/room")
        found = call(base, "/api/search", {"question": golden[0].question, "how": "rerank"})
        quality = call(base, "/api/quality")
        decisions = call(base, "/api/review", {"question": golden[0].question, "decision": "runbook", "passage": golden[0].passage})
        page = urllib.request.urlopen(base).read().decode()
    finally:
        server.shutdown()
    assert room["sections"] == 64 and room["ways"] == list(WAYS) and len(room["questions"]) == 3
    assert found["label"] == golden[0].passage and found["labelled"]["id"] == golden[0].passage
    assert set(found["ranks"]) == set(WAYS) and len(found["hits"]) == 5
    assert quality["total"] == 3
    assert decisions == [{"question": golden[0].question, "decision": "runbook", "passage": golden[0].passage}]
    assert 'data-surface="internal runbook search"' in page and "Needs a person" in page


def test_the_page_refuses_a_bad_way(search, tmp_path):
    server, base = start(Room(search, [], tmp_path / "review.jsonl"))
    try:
        with pytest.raises(urllib.error.HTTPError, match="400"):
            call(base, "/api/search", {"question": "x", "how": "magic"})
    finally:
        server.shutdown()


@pytest.mark.parametrize("headers", [{"Host": "attacker.example:80"}, {"Origin": "http://attacker.example"}])
def test_the_page_refuses_other_sites(search, tmp_path, headers):
    server, base = start(Room(search, [], tmp_path / "review.jsonl"))
    try:
        request = urllib.request.Request(f"{base}/api/review", data=b'{"question":"q","decision":"label"}', headers=headers, method="POST")
        with pytest.raises(urllib.error.HTTPError, match="403"):
            urllib.request.urlopen(request)
    finally:
        server.shutdown()
    assert not (tmp_path / "review.jsonl").exists()


def test_env_example_matches_the_code_defaults():
    env = dict(line.split("=", 1) for line in (LAB / "env.example").read_text().splitlines() if "=" in line and not line.startswith("#"))
    assert env == {"DENSE_MODEL": encoders.DENSE, "SPARSE_MODEL": encoders.SPARSE, "RERANK_MODEL": encoders.RERANK}
