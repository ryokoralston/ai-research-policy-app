"""Tests for mcp_server.py — the PolicyLibraryMCP FastMCP server exposing the
document library's hybrid retrieval and chunk storage as MCP tools.

Runs the tool functions directly (they're plain sync functions) against the
real dev DB (backend/data/research.db, 900+ chunks across 35+ indexed docs) —
read-only usage, no rows are modified. Expected values (a document title, a
doc_id, a chunk snippet) are looked up from the DB at test time rather than
hardcoded, so this stays valid as the library's contents change.

search_library loads the local sentence-transformers embedding model (and,
on first successful retrieval, the cross-encoder reranker) — that's the one
slow test here (~10-20s) and is run last.

Run from the backend directory:
    ./venv/bin/python -m tests.test_mcp_server
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import mcp_server
from database import SessionLocal
from models.document import Document, DocumentChunk


# ── Schema-level check: the 3 tools are registered on the FastMCP instance ──

def test_tools_registered():
    tools = mcp_server.mcp._tool_manager.list_tools()
    names = {t.name for t in tools}
    assert names == {"search_library", "read_document", "list_documents"}, names


def test_resources_registered():
    resource_manager = mcp_server.mcp._resource_manager
    resource_uris = {str(r.uri) for r in resource_manager.list_resources()}
    assert "docs://documents" in resource_uris, resource_uris

    template_uris = {t.uri_template for t in resource_manager.list_templates()}
    assert "docs://documents/{doc_id}" in template_uris, template_uris


# ── list_documents ───────────────────────────────────────────────────────────

def test_list_documents_contains_known_title():
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.status == "indexed").first()
        assert doc is not None, "expected at least one indexed document in the dev DB"
        expected_label = doc.title or doc.filename
    finally:
        db.close()

    result = mcp_server.list_documents()
    assert isinstance(result, str) and result, "expected a non-empty string"
    assert expected_label in result, (expected_label, result[:500])
    assert doc.id in result, (doc.id, result[:500])


def test_list_documents_empty_library_message():
    # Not exercising an actually-empty DB (it has real data and this test
    # must not mutate it) — just confirm the non-empty path never accidentally
    # returns the "no documents" sentinel while indexed docs exist.
    db = SessionLocal()
    try:
        has_indexed = db.query(Document).filter(Document.status == "indexed").first() is not None
    finally:
        db.close()
    assert has_indexed, "expected at least one indexed document in the dev DB"
    result = mcp_server.list_documents()
    assert result != "No indexed documents in the library.", result


# ── read_document ────────────────────────────────────────────────────────────

def test_read_document_known_id_contains_first_chunk():
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.status == "indexed").first()
        assert doc is not None
        first_chunk = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == doc.id)
            .order_by(DocumentChunk.chunk_index)
            .first()
        )
        assert first_chunk is not None, f"expected chunks for document {doc.id}"
        snippet = first_chunk.content.strip()[:200]
        expected_label = doc.title or doc.filename
    finally:
        db.close()

    text = mcp_server.read_document(doc.id)
    assert expected_label in text, (expected_label, text[:300])
    assert snippet in text, (snippet, text[:300])


def test_read_document_truncates_long_documents():
    db = SessionLocal()
    try:
        # Pick the document with the most chunks/words as the best candidate
        # for exceeding MAX_READ_DOCUMENT_CHARS; if even that one doesn't
        # exceed the cap, skip the truncation-note assertion (small dev DB).
        doc = (
            db.query(Document)
            .filter(Document.status == "indexed")
            .order_by(Document.word_count.desc())
            .first()
        )
        assert doc is not None
    finally:
        db.close()

    text = mcp_server.read_document(doc.id)
    assert len(text) <= mcp_server.MAX_READ_DOCUMENT_CHARS + len("\n\n[... truncated ...]")
    if doc.word_count and doc.word_count * 6 > mcp_server.MAX_READ_DOCUMENT_CHARS:
        # word_count*~6 chars is a rough over-estimate of rendered length
        assert "[... truncated ...]" in text, text[-100:]


def test_read_document_unknown_id_raises_value_error():
    raised = False
    try:
        mcp_server.read_document("this-doc-id-does-not-exist")
    except ValueError:
        raised = True
    assert raised, "expected ValueError for an unknown doc_id"


# ── docs://documents resource (list_docs) ────────────────────────────────────

def test_list_docs_contains_live_doc_id_with_expected_shape():
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.status == "indexed").first()
        assert doc is not None, "expected at least one indexed document in the dev DB"
        expected_id = doc.id
    finally:
        db.close()

    entries = mcp_server.list_docs()
    assert isinstance(entries, list) and entries, "expected a non-empty list"

    ids = {e["id"] for e in entries}
    assert expected_id in ids, (expected_id, ids)

    for entry in entries:
        assert set(entry.keys()) == {"id", "title", "pages", "words"}, entry
        assert entry["title"], entry


# ── docs://documents/{doc_id} resource (fetch_doc) ───────────────────────────

def test_fetch_doc_matches_read_document_tool():
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.status == "indexed").first()
        assert doc is not None, "expected at least one indexed document in the dev DB"
        doc_id = doc.id
    finally:
        db.close()

    assert mcp_server.fetch_doc(doc_id) == mcp_server.read_document(doc_id)


def test_fetch_doc_unknown_id_raises_value_error():
    raised = False
    try:
        mcp_server.fetch_doc("no-such-id")
    except ValueError:
        raised = True
    assert raised, "expected ValueError for an unknown doc_id"


# ── Prompts ───────────────────────────────────────────────────────────────

def test_prompts_registered():
    prompts = {p.name: p for p in mcp_server.mcp._prompt_manager.list_prompts()}
    assert set(prompts) == {"summarize_document", "policy_brief"}, set(prompts)

    summarize = prompts["summarize_document"]
    assert summarize.description and summarize.description.strip()
    arg_names = {a.name for a in (summarize.arguments or [])}
    assert arg_names == {"doc_id"}, arg_names
    assert all(a.required for a in summarize.arguments), summarize.arguments

    brief = prompts["policy_brief"]
    assert brief.description and brief.description.strip()
    arg_names = {a.name for a in (brief.arguments or [])}
    assert arg_names == {"topic"}, arg_names
    assert all(a.required for a in brief.arguments), brief.arguments


def test_summarize_document_prompt_mentions_doc_id_and_read_document():
    messages = mcp_server.summarize_document_prompt("some-doc-id")
    assert isinstance(messages, list) and len(messages) == 1, messages

    message = messages[0]
    assert message.role == "user", message.role
    text = message.content.text
    assert "some-doc-id" in text, text
    assert "read_document" in text, text


def test_policy_brief_prompt_mentions_topic_and_search_library():
    messages = mcp_server.policy_brief_prompt("AI liability")
    assert isinstance(messages, list) and len(messages) == 1, messages

    message = messages[0]
    assert message.role == "user", message.role
    text = message.content.text
    assert "AI liability" in text, text
    assert "search_library" in text, text


# ── search_library (slow: loads the embedding model) ────────────────────────

def test_search_library_returns_numbered_results_with_doc_ids():
    result = mcp_server.search_library("artificial intelligence", top_k=3)
    assert isinstance(result, str) and result, "expected a non-empty string"

    import re
    numbers = re.findall(r"(?m)^(\d+)\. ", result)
    assert numbers == ["1", "2", "3"], (numbers, result[:500])

    doc_ids = re.findall(r"doc_id=(\S+)", result)
    assert len(doc_ids) == 3, (doc_ids, result[:500])


def test_search_library_empty_results_message():
    # Hybrid retrieval over a non-empty collection always returns *something*
    # for any non-empty query (RRF fusion has no relevance floor), so the
    # empty-results message can't be reached with a real query against the
    # populated dev DB. Monkeypatch the cached retriever with a fake that
    # returns no chunks to exercise that branch directly and honestly.
    class _EmptyRetriever:
        def retrieve(self, question, top_k=5):
            return []

    original = mcp_server._retriever
    mcp_server._retriever = _EmptyRetriever()
    try:
        result = mcp_server.search_library("zzz-no-such-query-zzz", top_k=3)
    finally:
        mcp_server._retriever = original

    assert "No results found" in result, result


# ── Test runner ──────────────────────────────────────────────────────────────

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
    print("\nRunning mcp_server.py tests...\n")

    _run("tools registered", test_tools_registered)
    _run("resources registered", test_resources_registered)
    _run("list_documents contains known title", test_list_documents_contains_known_title)
    _run("list_documents empty-library message not hit", test_list_documents_empty_library_message)
    _run("read_document known id contains first chunk", test_read_document_known_id_contains_first_chunk)
    _run("read_document truncates long documents", test_read_document_truncates_long_documents)
    _run("read_document unknown id raises ValueError", test_read_document_unknown_id_raises_value_error)
    _run("list_docs contains live doc id with expected shape", test_list_docs_contains_live_doc_id_with_expected_shape)
    _run("fetch_doc matches read_document tool", test_fetch_doc_matches_read_document_tool)
    _run("fetch_doc unknown id raises ValueError", test_fetch_doc_unknown_id_raises_value_error)
    _run("prompts registered", test_prompts_registered)
    _run("summarize_document_prompt mentions doc_id and read_document", test_summarize_document_prompt_mentions_doc_id_and_read_document)
    _run("policy_brief_prompt mentions topic and search_library", test_policy_brief_prompt_mentions_topic_and_search_library)
    _run("search_library empty-results message path", test_search_library_empty_results_message)
    # Slowest last: loads the embedding model (+ reranker on first retrieve).
    _run("search_library returns numbered results with doc_ids", test_search_library_returns_numbered_results_with_doc_ids)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
