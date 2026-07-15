"""Tests for answer_question's search_documents tool execution.

Pins the tool-result context format ([Title, p.N, sec: S]) and the citation
dedupe across the batched document-title lookup (previously one DB query per
chunk). Retriever and stream_chat_with_tools are faked — no models, no API.

Run from the backend directory:
    ./venv/bin/python -m tests.test_rag_answer
"""
import asyncio
import json
import os
import sys
import types
import uuid

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

for _name in ("chromadb", "sentence_transformers"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models import Document
from rag.vector_store import RetrievedChunk
import rag.retriever as retriever_module
import services.rag_service as rag_service


def _chunk(chunk_id, doc_id, content, page=3, section="Findings", context=""):
    return RetrievedChunk(
        chunk_id=chunk_id, doc_id=doc_id, content=content,
        page_number=page, section_header=section, score=0.9, chunk_index=0,
        context=context,
    )


def _run_answer_multi(chunk_batches: list[list]):
    """Run answer_question with fakes, calling tool_executor once per batch in
    chunk_batches (simulates a follow-up search within the same turn).
    Return (events, list of captured tool results, in call order).
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Document(id="doc-titled", filename="f.pdf", title="EU AI Act Analysis",
                    source_type="upload", status="indexed"))
    db.add(Document(id="doc-untitled", filename="notes.txt", title=None,
                    source_type="upload", status="indexed"))
    db.commit()

    class FakeRetriever:
        def __init__(self):
            self._calls = 0

        def retrieve(self, query, top_k=5, doc_ids=None):
            batch = chunk_batches[self._calls]
            self._calls += 1
            return batch

    captured: dict = {"tool_results": []}

    async def fake_stream_chat_with_tools(messages, system="", tools=None,
                                          tool_executor=None, **kwargs):
        for _ in chunk_batches:
            result = await tool_executor("search_documents", {"query": "test"})
            captured["tool_results"].append(result)
            yield ("tool_use", {"name": "search_documents", "input": {"query": "test"}})
        yield ("text", "The answer.")

    orig_retriever = retriever_module.Retriever
    orig_stream = rag_service.stream_chat_with_tools
    retriever_module.Retriever = FakeRetriever
    rag_service.stream_chat_with_tools = fake_stream_chat_with_tools
    try:
        async def collect():
            return [e async for e in rag_service.answer_question("q?", None, 5, db)]
        events = asyncio.run(collect())
    finally:
        retriever_module.Retriever = orig_retriever
        rag_service.stream_chat_with_tools = orig_stream
        db.close()

    return events, captured["tool_results"]


def _run_answer(chunks):
    """Single-batch convenience wrapper around _run_answer_multi."""
    events, tool_results = _run_answer_multi([chunks])
    return events, tool_results[0] if tool_results else ""


def test_context_format_with_batched_titles():
    chunks = [
        _chunk("c1", "doc-titled", "First passage."),
        _chunk("c2", "doc-untitled", "Second passage.", page=1, section="Intro"),
        _chunk("c3", "doc-missing", "Orphan passage."),
    ]
    events, tool_result = _run_answer(chunks)

    assert "<source_documents>" in tool_result
    # Numbered (1-based, first-seen order), then title, filename fallback,
    # and Unknown fallback — exact new format
    assert "[1] [EU AI Act Analysis, p.3, sec: Findings]\nFirst passage." in tool_result
    assert "[2] [notes.txt, p.1, sec: Intro]\nSecond passage." in tool_result
    assert "[3] [Unknown, p.3, sec: Findings]\nOrphan passage." in tool_result


def test_citations_deduped_by_chunk_id():
    chunks = [
        _chunk("c1", "doc-titled", "P1"),
        _chunk("c1", "doc-titled", "P1"),  # duplicate chunk_id
        _chunk("c2", "doc-untitled", "P2"),
    ]
    events, _ = _run_answer(chunks)

    complete = [e for e in events if e.startswith("event: complete")][0]
    payload = json.loads(complete.split("data: ", 1)[1].strip())
    cited_ids = [c["chunk_id"] for c in payload["citations"]]
    assert cited_ids == ["c1", "c2"], cited_ids
    assert payload["citations"][0]["index"] == 1
    assert payload["citations"][1]["index"] == 2


def test_citation_index_stable_across_followup_search():
    """A follow-up search within the same turn that re-retrieves an overlapping
    chunk_id must reuse its original index (no renumbering) and must not
    duplicate the citations list entry."""
    batch1 = [
        _chunk("c1", "doc-titled", "P1"),
        _chunk("c2", "doc-untitled", "P2", page=1, section="Intro"),
    ]
    batch2 = [
        _chunk("c2", "doc-untitled", "P2", page=1, section="Intro"),  # already seen -> index 2
        _chunk("c3", "doc-titled", "P3"),  # new -> index 3
    ]
    events, tool_results = _run_answer_multi([batch1, batch2])

    # First call assigns c1->1, c2->2
    assert "[1] [EU AI Act Analysis, p.3, sec: Findings]\nP1" in tool_results[0]
    assert "[2] [notes.txt, p.1, sec: Intro]\nP2" in tool_results[0]

    # Second call: c2 keeps index 2 (not renumbered to 3), c3 gets the next free index 3
    assert "[2] [notes.txt, p.1, sec: Intro]\nP2" in tool_results[1]
    assert "[3] [EU AI Act Analysis, p.3, sec: Findings]\nP3" in tool_results[1]

    complete = [e for e in events if e.startswith("event: complete")][0]
    payload = json.loads(complete.split("data: ", 1)[1].strip())
    citations = payload["citations"]
    # Exactly 3 unique citations, not 4 — c2 must not be duplicated
    assert len(citations) == 3, citations
    by_id = {c["chunk_id"]: c["index"] for c in citations}
    assert by_id == {"c1": 1, "c2": 2, "c3": 3}, by_id


def test_context_line_included_when_present_omitted_when_absent():
    """Contextual Retrieval (rag/contextualizer.py): a "[Context: ...]" line
    follows the [N] header only when chunk.context is non-empty; the
    citation snippet stays the original content either way."""
    chunks = [
        _chunk("c1", "doc-titled", "First passage.", context="Situates c1 in the doc."),
        _chunk("c2", "doc-untitled", "Second passage.", page=1, section="Intro"),  # no context
    ]
    events, tool_result = _run_answer(chunks)

    assert "[1] [EU AI Act Analysis, p.3, sec: Findings]\n[Context: Situates c1 in the doc.]\nFirst passage." in tool_result
    assert "[2] [notes.txt, p.1, sec: Intro]\nSecond passage." in tool_result
    assert "[Context:" not in tool_result.split("[2]", 1)[1]

    complete = [e for e in events if e.startswith("event: complete")][0]
    payload = json.loads(complete.split("data: ", 1)[1].strip())
    # Citation snippet is original content only — never the context.
    assert payload["citations"][0]["snippet"] == "First passage."


def test_no_results_message():
    events, tool_result = _run_answer([])
    assert tool_result == "No relevant content found in the document library for this query."


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
    print("\nRunning rag answer tests...\n")

    _run("context format with batched titles", test_context_format_with_batched_titles)
    _run("citations deduped by chunk_id", test_citations_deduped_by_chunk_id)
    _run("citation index stable across follow-up search", test_citation_index_stable_across_followup_search)
    _run("context line included when present, omitted when absent", test_context_line_included_when_present_omitted_when_absent)
    _run("no-results message", test_no_results_message)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
