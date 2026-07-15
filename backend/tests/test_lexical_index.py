"""Tests for rag/lexical_index.py — SQLite FTS5-backed BM25 lexical search.

Each test constructs its own LexicalIndex pointed at a fresh temp-file path
(passed directly to the constructor, so no monkeypatching of config/settings
is needed and tests can run fully in parallel/isolated).

Run from the backend directory:
    ./venv/bin/python -m tests.test_lexical_index
"""
import os
import sqlite3
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


# ── Schema v2: display/match/context split ──────────────────────────────────

def test_add_chunks_display_and_context_split():
    """content (matched) can differ from display_content (returned) and
    carries a separate context — the Contextual Retrieval schema (see
    rag/contextualizer.py / rag/lexical_index.py's _ensure_schema)."""
    path = _tmp_index_path()
    try:
        idx = LexicalIndex(db_path=path)
        idx.add_chunks(
            chunk_ids=["c1"],
            documents=["situating context here\n\noriginal chunk text about INC-2023"],
            metadatas=[_meta()],
            display_documents=["original chunk text about INC-2023"],
            contexts=["situating context here"],
        )
        results = idx.search("INC-2023", n_results=10)
        assert len(results) == 1, results
        # Returned content is the DISPLAY text, not the combined match text.
        assert results[0].content == "original chunk text about INC-2023", results
        assert results[0].context == "situating context here", results

        # A term that only appears in the context (not the display text)
        # still matches, because `content` (matched) includes it.
        by_context_term = idx.search("situating", n_results=10)
        assert len(by_context_term) == 1, by_context_term
    finally:
        idx_cleanup(path)


def test_add_chunks_without_display_or_context_defaults_backward_compatible():
    """Callers that don't pass display_documents/contexts (pre-Contextual-
    Retrieval call sites) get the old behavior exactly: content==documents,
    context=='' — this is what makes the new optional params backward
    compatible."""
    path = _tmp_index_path()
    try:
        idx = LexicalIndex(db_path=path)
        idx.add_chunks(
            chunk_ids=["c1"],
            documents=["plain chunk text"],
            metadatas=[_meta()],
        )
        results = idx.search("plain", n_results=10)
        assert len(results) == 1, results
        assert results[0].content == "plain chunk text", results
        assert results[0].context == "", results
    finally:
        idx_cleanup(path)


# ── Legacy chunk_fts -> chunk_fts2 migration ─────────────────────────────────

def test_legacy_chunk_fts_migrates_to_v2_with_zero_data_loss():
    """A user restarting the app before any Contextual-Retrieval backfill
    must keep working: rows in a pre-existing legacy chunk_fts table are
    copied into chunk_fts2 (display_content=content, context='') and the
    legacy table is dropped, all inside LexicalIndex.__init__."""
    path = _tmp_index_path()
    try:
        # Build the legacy schema by hand, bypassing LexicalIndex entirely,
        # to simulate a pre-Contextual-Retrieval on-disk index.
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE VIRTUAL TABLE chunk_fts USING fts5("
            "content, chunk_id UNINDEXED, doc_id UNINDEXED, "
            "page_number UNINDEXED, section_header UNINDEXED, "
            "chunk_index UNINDEXED)"
        )
        conn.execute(
            "INSERT INTO chunk_fts (content, chunk_id, doc_id, page_number, "
            "section_header, chunk_index) VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy incident report INC-9999", "legacy-c1", "doc-legacy", 2, "Intro", 0),
        )
        conn.commit()
        conn.close()

        idx = LexicalIndex(db_path=path)  # __init__ -> _ensure_schema migrates + drops legacy

        # Legacy table is gone; chunk_fts2 has the migrated row.
        conn = sqlite3.connect(path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "chunk_fts" not in tables, tables
        assert "chunk_fts2" in tables, tables

        assert idx.count() == 1
        results = idx.search("INC-9999", n_results=10)
        assert len(results) == 1, results
        assert results[0].chunk_id == "legacy-c1"
        assert results[0].doc_id == "doc-legacy"
        assert results[0].content == "legacy incident report INC-9999", results
        assert results[0].context == "", "migrated legacy rows have no context"
        assert results[0].page_number == 2
        assert results[0].section_header == "Intro"
    finally:
        idx_cleanup(path)


def test_migration_runs_once_second_init_is_a_noop():
    """A second LexicalIndex() against the same path (legacy already
    migrated/dropped) must not error and must not duplicate rows."""
    path = _tmp_index_path()
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE VIRTUAL TABLE chunk_fts USING fts5("
            "content, chunk_id UNINDEXED, doc_id UNINDEXED, "
            "page_number UNINDEXED, section_header UNINDEXED, "
            "chunk_index UNINDEXED)"
        )
        conn.execute(
            "INSERT INTO chunk_fts (content, chunk_id, doc_id, page_number, "
            "section_header, chunk_index) VALUES (?, ?, ?, ?, ?, ?)",
            ("one legacy row", "c1", "doc-a", 1, "", 0),
        )
        conn.commit()
        conn.close()

        LexicalIndex(db_path=path)
        idx2 = LexicalIndex(db_path=path)  # second init: no legacy table left
        assert idx2.count() == 1
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
    _run("add_chunks display/context split", test_add_chunks_display_and_context_split)
    _run("add_chunks without display/context defaults backward compatible", test_add_chunks_without_display_or_context_defaults_backward_compatible)
    _run("legacy chunk_fts migrates to v2 with zero data loss", test_legacy_chunk_fts_migrates_to_v2_with_zero_data_loss)
    _run("migration runs once, second init is a no-op", test_migration_runs_once_second_init_is_a_noop)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
