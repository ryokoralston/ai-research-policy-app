"""Tests for rag/retriever.py's rrf_fuse — Reciprocal Rank Fusion of ranked
candidate lists.

Deliberately imports ONLY rrf_fuse and RetrievedChunk (not Retriever itself),
so this test never loads ChromaDB, the embedding model, or the cross-encoder
— it exercises pure fusion logic against hand-built fake chunks.

Run from the backend directory:
    ./venv/bin/python -m tests.test_retriever_fusion
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from rag.retriever import rrf_fuse
from rag.vector_store import RetrievedChunk


def _chunk(chunk_id, doc_id="doc-1", chunk_index=0, score=0.0):
    return RetrievedChunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        content=f"content for {chunk_id}",
        page_number=1,
        section_header="",
        score=score,
        chunk_index=chunk_index,
    )


# ── The lesson's worked example ─────────────────────────────────────────────

def test_worked_example_k1():
    s2, s7, s6 = _chunk("S2"), _chunk("S7"), _chunk("S6")
    s6_b, s2_b, s7_b = _chunk("S6"), _chunk("S2"), _chunk("S7")

    ranking_a = [s2, s7, s6]   # index A: S2, S7, S6
    ranking_b = [s6_b, s2_b, s7_b]  # index B: S6, S2, S7

    fused = rrf_fuse([ranking_a, ranking_b], k=1)

    fused_ids = [c.chunk_id for c in fused]
    assert fused_ids == ["S2", "S6", "S7"], fused_ids

    # Cross-check the scores by hand, computed the same way rrf_fuse does.
    # S2: rank1 in A -> 1/(1+1)=0.5, rank2 in B -> 1/(1+2)=0.3333... -> 0.8333...
    # S6: rank3 in A -> 1/(1+3)=0.25, rank1 in B -> 1/(1+1)=0.5 -> 0.75
    # S7: rank2 in A -> 1/(1+2)=0.3333..., rank3 in B -> 1/(1+3)=0.25 -> 0.5833...
    expected = {"S2": 1 / 2 + 1 / 3, "S6": 1 / 4 + 1 / 2, "S7": 1 / 3 + 1 / 4}
    computed = {}
    for ranking in (ranking_a, ranking_b):
        for rank, chunk in enumerate(ranking, start=1):
            computed[chunk.chunk_id] = computed.get(chunk.chunk_id, 0.0) + 1.0 / (1 + rank)
    for cid, val in expected.items():
        assert abs(computed[cid] - val) < 1e-9, (cid, computed[cid], val)


# ── Chunk in only one list still ranks ──────────────────────────────────────

def test_chunk_in_only_one_list_still_ranks():
    only_in_a = _chunk("only-a")
    shared = _chunk("shared")
    shared_b = _chunk("shared")

    ranking_a = [shared, only_in_a]
    ranking_b = [shared_b]

    fused = rrf_fuse([ranking_a, ranking_b], k=60)
    fused_ids = {c.chunk_id for c in fused}
    assert fused_ids == {"only-a", "shared"}, fused_ids
    # shared gets contributions from both lists, only_in_a from just one,
    # so shared should outrank only_in_a.
    assert [c.chunk_id for c in fused][0] == "shared", fused


# ── Dedup: unique chunk_ids, first-seen representative kept ────────────────

def test_dedup_keeps_first_seen_representative():
    first_instance = _chunk("dup", score=111.0)
    second_instance = _chunk("dup", score=222.0)

    fused = rrf_fuse([[first_instance], [second_instance]], k=60)

    ids = [c.chunk_id for c in fused]
    assert ids == ["dup"], ids
    assert len(fused) == len(set(c.chunk_id for c in fused))
    # representative must be the first-seen instance (identity + distinguishing field)
    assert fused[0] is first_instance
    assert fused[0].score == 111.0, fused[0].score


# ── Empty inputs ─────────────────────────────────────────────────────────────

def test_empty_rankings_list():
    assert rrf_fuse([]) == []


def test_empty_inner_lists():
    assert rrf_fuse([[], []]) == []


def test_mix_of_empty_and_nonempty_inner_lists():
    c1 = _chunk("c1")
    fused = rrf_fuse([[], [c1], []], k=60)
    assert [c.chunk_id for c in fused] == ["c1"], fused


# ── Different-length lists don't raise ──────────────────────────────────────

def test_different_length_lists_do_not_raise():
    a = [_chunk("a1"), _chunk("a2"), _chunk("a3"), _chunk("a4")]
    b = [_chunk("a1")]
    fused = rrf_fuse([a, b], k=60)
    ids = {c.chunk_id for c in fused}
    assert ids == {"a1", "a2", "a3", "a4"}, ids
    # a1 appears in both lists, so it should rank first.
    assert fused[0].chunk_id == "a1", fused


# ── Determinism on ties ─────────────────────────────────────────────────────

def test_determinism_on_ties():
    # Two chunks that never co-occur in any list get equal (single-list,
    # same-rank) scores -> must break ties deterministically by first-seen
    # order, and repeated calls must produce the same result.
    a = [_chunk("x")]
    b = [_chunk("y")]
    fused_1 = rrf_fuse([a, b], k=60)
    fused_2 = rrf_fuse([a, b], k=60)
    ids_1 = [c.chunk_id for c in fused_1]
    ids_2 = [c.chunk_id for c in fused_2]
    assert ids_1 == ids_2, (ids_1, ids_2)
    # x was first-seen (list a comes first), so it wins the tie.
    assert ids_1 == ["x", "y"], ids_1


# ── Input chunks' .score fields are unchanged after fusion ─────────────────

def test_input_scores_unchanged():
    c1 = _chunk("c1", score=0.42)
    c2 = _chunk("c2", score=0.99)
    original_scores = {"c1": c1.score, "c2": c2.score}

    fused = rrf_fuse([[c1], [c2, c1]], k=60)

    assert c1.score == original_scores["c1"], c1.score
    assert c2.score == original_scores["c2"], c2.score
    for c in fused:
        assert c.score == original_scores[c.chunk_id], (c.chunk_id, c.score)


# ── Test runner ────────────────────────────────────────────────────────────

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
    print("\nRunning retriever fusion (rrf_fuse) tests...\n")

    _run("worked example k=1", test_worked_example_k1)
    _run("chunk in only one list still ranks", test_chunk_in_only_one_list_still_ranks)
    _run("dedup keeps first-seen representative", test_dedup_keeps_first_seen_representative)
    _run("empty rankings list", test_empty_rankings_list)
    _run("empty inner lists", test_empty_inner_lists)
    _run("mix of empty and nonempty inner lists", test_mix_of_empty_and_nonempty_inner_lists)
    _run("different-length lists do not raise", test_different_length_lists_do_not_raise)
    _run("determinism on ties", test_determinism_on_ties)
    _run("input scores unchanged", test_input_scores_unchanged)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
