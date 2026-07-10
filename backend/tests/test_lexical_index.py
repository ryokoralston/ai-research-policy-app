"""Tests for rag/lexical_index.py — SQLite FTS5-backed BM25 lexical search.

Each test constructs its own LexicalIndex pointed at a fresh temp-file path
(passed directly to the constructor, so no monkeypatching of config/settings
is needed and tests can run fully in parallel/isolated).

Run from the backend directory:
    ./venv/bin/python -m tests.test_lexical_index
"""
import os
import sys
import tempfile

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from rag.lexical_index import LexicalIndex


def _tmp_index_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="bm25_test_")
    os.close(fd)
    os.remove(path)  # LexicalIndex creates the file itself on first connect
    return path


def _meta(doc_id="doc-1", page_number=1, section_header="", chunk_index=0):
    return {
        "doc_id": doc_id,
        "page_number": page_number,
        "section_header": section_header,
        "chunk_index": chunk_index,
    }


# ── Ranking: exact identifier beats wordy semantic distractor ──────────────

def test_exact_rare_term_ranks_first():
    path = _tmp_index_path()
    try:
        idx = LexicalIndex(db_path=path)
        idx.add_chunks(
            chunk_ids=["c1", "c2"],
            documents=[
                "This document discusses general AI governance, safety frameworks, "
                "international cooperation, and long-term policy considerations for "
                "artificial intelligence regulation across multiple jurisdictions.",
                "Incident report INC-2023-Q4-011 describes a model deployment failure.",
            ],
            metadatas=[_meta(chunk_index=0), _meta(chunk_index=1)],
        )
        results = idx.search("INC-2023-Q4-011", n_results=10)
        assert len(results) >= 1, results
        assert results[0].chunk_id == "c2", results
        # Higher score = more relevant (negated bm25).
        if len(results) > 1:
            assert results[0].score >= results[1].score, results
    finally:
        idx_cleanup(path)


# ── Hostile query strings must never raise ──────────────────────────────────

def test_hostile_queries_do_not_raise():
    path = _tmp_index_path()
    try:
        idx = LexicalIndex(db_path=path)
        idx.add_chunks(
            chunk_ids=["c1"],
            documents=["Incident report INC-2023-Q4-011 describes a deployment failure."],
            metadatas=[_meta()],
        )
        hostile_queries = [
            'a "quoted" thing',
            "INC-2023-Q4-011",
            "foo AND (bar OR baz) NOT qux:col",
            "",
            "   ",
        ]
        for q in hostile_queries:
            results = idx.search(q, n_results=10)
            assert isinstance(results, list), (q, results)
    finally:
        idx_cleanup(path)


def test_empty_and_whitespace_query_returns_empty_list():
    path = _tmp_index_path()
    try:
        idx = LexicalIndex(db_path=path)
        idx.add_chunks(
            chunk_ids=["c1"],
            documents=["some content here"],
            metadatas=[_meta()],
        )
        assert idx.search("", n_results=10) == []
        assert idx.search("   ", n_results=10) == []
    finally:
        idx_cleanup(path)


# ── doc_ids filter ───────────────────────────────────────────────────────────

def test_doc_ids_filter_restricts_results():
    path = _tmp_index_path()
    try:
        idx = LexicalIndex(db_path=path)
        idx.add_chunks(
            chunk_ids=["c1", "c2"],
            documents=[
                "policy report about artificial intelligence",
                "policy report about artificial intelligence",
            ],
            metadatas=[_meta(doc_id="doc-a"), _meta(doc_id="doc-b")],
        )
        all_results = idx.search("artificial intelligence", n_results=10)
        assert {r.doc_id for r in all_results} == {"doc-a", "doc-b"}, all_results

        filtered = idx.search("artificial intelligence", n_results=10, doc_ids=["doc-a"])
        assert len(filtered) == 1, filtered
        assert filtered[0].doc_id == "doc-a", filtered
    finally:
        idx_cleanup(path)


# ── delete_document ──────────────────────────────────────────────────────────

def test_delete_document_removes_its_chunks():
    path = _tmp_index_path()
    try:
        idx = LexicalIndex(db_path=path)
        idx.add_chunks(
            chunk_ids=["c1", "c2", "c3"],
            documents=["alpha text", "beta text", "gamma text"],
            metadatas=[
                _meta(doc_id="doc-a", chunk_index=0),
                _meta(doc_id="doc-a", chunk_index=1),
                _meta(doc_id="doc-b", chunk_index=0),
            ],
        )
        assert idx.count() == 3

        idx.delete_document("doc-a")

        assert idx.count() == 1
        results = idx.search("alpha OR beta OR gamma", n_results=10)
        assert all(r.doc_id != "doc-a" for r in results), results
        assert any(r.doc_id == "doc-b" for r in results), results
    finally:
        idx_cleanup(path)


# ── clear() + count() ─────────────────────────────────────────────────────────

def test_clear_and_count():
    path = _tmp_index_path()
    try:
        idx = LexicalIndex(db_path=path)
        assert idx.count() == 0

        idx.add_chunks(
            chunk_ids=["c1", "c2"],
            documents=["first chunk", "second chunk"],
            metadatas=[_meta(chunk_index=0), _meta(chunk_index=1)],
        )
        assert idx.count() == 2

        idx.clear()
        assert idx.count() == 0
        assert idx.search("first OR second", n_results=10) == []
    finally:
        idx_cleanup(path)


# ── cleanup helper ────────────────────────────────────────────────────────────

def idx_cleanup(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)


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
    print("\nRunning lexical_index tests...\n")

    _run("exact rare term ranks first", test_exact_rare_term_ranks_first)
    _run("hostile queries do not raise", test_hostile_queries_do_not_raise)
    _run("empty/whitespace query returns []", test_empty_and_whitespace_query_returns_empty_list)
    _run("doc_ids filter restricts results", test_doc_ids_filter_restricts_results)
    _run("delete_document removes its chunks", test_delete_document_removes_its_chunks)
    _run("clear() and count()", test_clear_and_count)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
