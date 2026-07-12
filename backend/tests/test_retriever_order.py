"""Tests for Retriever reading-order sorting (rag/retriever.py).

The final sort used to be `top.sort(key=lambda c: c.chunk_id)` — but chunk_id
is a random UUID4, so the "reading order" the comment promised was actually a
shuffle that also destroyed the cross-encoder rerank order. The fix sorts by
(doc_id, chunk_index).

Covers:
  1. Rerank path: top_k selection follows cross-encoder scores, final order
     is (doc_id, chunk_index)
  2. Fallback path (cross-encoder unavailable): first top_k candidates by
     vector similarity, still returned in reading order

chromadb / sentence-transformers are NOT required: both are stubbed in
sys.modules before importing rag.retriever (chromadb is only touched at
VectorStore.__init__, which these tests never call; CrossEncoder is imported
inside retrieve() and swapped per test). The BM25 LexicalIndex leg is stubbed
with _FakeLexicalIndex (empty results) so retrieve()'s RRF fusion step
degenerates to the single vector ranking — order-preserving — keeping these
tests focused on reading-order sort and reranker-caching semantics rather
than fusion (which test_retriever_fusion.py covers separately).

Run from the backend directory:
    ./venv/bin/python -m tests.test_retriever_order
"""
import os
import sys
import types
import uuid

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# ── Stub heavy optional deps before importing the module under test ───────────
if "chromadb" not in sys.modules:
    sys.modules["chromadb"] = types.ModuleType("chromadb")

_st_stub = types.ModuleType("sentence_transformers")
sys.modules["sentence_transformers"] = _st_stub

from rag.retriever import Retriever, _load_reranker
from rag.vector_store import RetrievedChunk


# ── Fakes ─────────────────────────────────────────────────────────────────────

def _chunk(doc_id: str, chunk_index: int, content: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(uuid.uuid4()),  # random, like production — must not affect order
        doc_id=doc_id,
        content=content,
        page_number=None,
        section_header=None,
        score=score,
        chunk_index=chunk_index,
    )


class _FakeVectorStore:
    def __init__(self, candidates):
        self._candidates = candidates

    def count(self):
        return len(self._candidates)

    def query(self, query_embedding, n_results=20, where=None):
        return self._candidates[:n_results]


class _FakeEmbedding:
    def embed_query(self, query):
        return [0.0]


class _FakeLexicalIndex:
    """Stub for the BM25 LexicalIndex — these tests are only concerned with
    reading-order sorting and reranker-caching, not fusion, so the lexical
    leg contributes no candidates. rrf_fuse over a single (vector-only)
    ranking list preserves that list's relative order, so this stub keeps
    the vector-order assertions below valid unchanged."""

    def search(self, query, n_results=20, doc_ids=None):
        return []


def _make_retriever(candidates) -> Retriever:
    r = Retriever.__new__(Retriever)  # skip __init__ (would touch chromadb)
    r._vs = _FakeVectorStore(candidates)
    r._embed = _FakeEmbedding()
    r._lexical = _FakeLexicalIndex()
    return r


class _ScoredCrossEncoder:
    """Scores by content: 'rank=N' in the chunk content → score N."""

    instances = 0  # counts constructions to verify the lru_cache reuse

    def __init__(self, model_name):
        _ScoredCrossEncoder.instances += 1

    def predict(self, pairs):
        return [float(content.split("rank=")[1]) for _, content in pairs]


class _BrokenCrossEncoder:
    def __init__(self, model_name):
        raise RuntimeError("model unavailable")


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_rerank_path_returns_reading_order():
    """Selection = top rerank scores; presentation = (doc_id, chunk_index)."""
    _load_reranker.cache_clear()
    candidates = [
        _chunk("doc-B", 7, "rank=5", 0.9),
        _chunk("doc-A", 3, "rank=1", 0.8),   # low rerank score → dropped
        _chunk("doc-A", 12, "rank=4", 0.7),
        _chunk("doc-B", 2, "rank=3", 0.6),
        _chunk("doc-A", 5, "rank=2", 0.5),   # low rerank score → dropped
    ]
    _st_stub.CrossEncoder = _ScoredCrossEncoder

    result = _make_retriever(candidates).retrieve("q", top_k=3)

    got = [(c.doc_id, c.chunk_index) for c in result]
    # rank=5,4,3 selected → reading order: doc-A#12, doc-B#2, doc-B#7
    expected = [("doc-A", 12), ("doc-B", 2), ("doc-B", 7)]
    assert got == expected, f"expected {expected}, got {got}"


def test_fallback_path_returns_reading_order():
    """Cross-encoder failure → first top_k by vector order, in reading order."""
    _load_reranker.cache_clear()
    candidates = [
        _chunk("doc-B", 9, "x", 0.9),
        _chunk("doc-A", 4, "x", 0.8),
        _chunk("doc-B", 1, "x", 0.7),
        _chunk("doc-A", 0, "x", 0.6),  # beyond top_k → dropped
    ]
    _st_stub.CrossEncoder = _BrokenCrossEncoder

    result = _make_retriever(candidates).retrieve("q", top_k=3)

    got = [(c.doc_id, c.chunk_index) for c in result]
    expected = [("doc-A", 4), ("doc-B", 1), ("doc-B", 9)]
    assert got == expected, f"expected {expected}, got {got}"


def test_empty_candidates_return_empty():
    _load_reranker.cache_clear()
    _st_stub.CrossEncoder = _ScoredCrossEncoder
    result = _make_retriever([]).retrieve("q", top_k=3)
    assert result == [], result


def test_reranker_is_loaded_once_across_queries():
    """Repeated retrieve() calls reuse one cached CrossEncoder instance."""
    _load_reranker.cache_clear()
    _st_stub.CrossEncoder = _ScoredCrossEncoder
    _ScoredCrossEncoder.instances = 0

    candidates = [_chunk("doc-A", 1, "rank=1", 0.9), _chunk("doc-A", 2, "rank=2", 0.8)]
    retriever = _make_retriever(candidates)
    for _ in range(3):
        retriever.retrieve("q", top_k=2)

    assert _ScoredCrossEncoder.instances == 1, (
        f"expected 1 construction, got {_ScoredCrossEncoder.instances}"
    )


def test_failed_reranker_load_is_not_cached():
    """A broken load falls back, then a later successful load is picked up."""
    _load_reranker.cache_clear()
    candidates = [_chunk("doc-A", 1, "rank=1", 0.9), _chunk("doc-A", 2, "rank=2", 0.8)]
    retriever = _make_retriever(candidates)

    _st_stub.CrossEncoder = _BrokenCrossEncoder
    result = retriever.retrieve("q", top_k=1)  # fallback: vector order
    assert [(c.doc_id, c.chunk_index) for c in result] == [("doc-A", 1)], result

    _st_stub.CrossEncoder = _ScoredCrossEncoder  # "model becomes available"
    result = retriever.retrieve("q", top_k=1)   # rerank: rank=2 wins
    assert [(c.doc_id, c.chunk_index) for c in result] == [("doc-A", 2)], result


# ── Test runner ───────────────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")


if __name__ == "__main__":
    print("\nRunning retriever reading-order tests...\n")

    _run("rerank path returns reading order", test_rerank_path_returns_reading_order)
    _run("fallback path returns reading order", test_fallback_path_returns_reading_order)
    _run("empty candidates return empty list", test_empty_candidates_return_empty)
    _run("reranker is loaded once across queries", test_reranker_is_loaded_once_across_queries)
    _run("failed reranker load is not cached", test_failed_reranker_load_is_not_cached)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
