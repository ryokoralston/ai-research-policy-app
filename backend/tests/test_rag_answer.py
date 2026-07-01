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


def _chunk(chunk_id, doc_id, content, page=3, section="Findings"):
    return RetrievedChunk(
        chunk_id=chunk_id, doc_id=doc_id, content=content,
        page_number=page, section_header=section, score=0.9, chunk_index=0,
    )


def _run_answer(chunks):
    """Run answer_question with fakes; return (events, captured tool result)."""
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
        def retrieve(self, query, top_k=5, doc_ids=None):
            return chunks

    captured: dict = {}

    async def fake_stream_chat_with_tools(messages, system="", tools=None,
                                          tool_executor=None, **kwargs):
        result = await tool_executor("search_documents", {"query": "test"})
        captured["tool_result"] = result
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

    return events, captured.get("tool_result", "")


def test_context_format_with_batched_titles():
    chunks = [
        _chunk("c1", "doc-titled", "First passage."),
        _chunk("c2", "doc-untitled", "Second passage.", page=1, section="Intro"),
        _chunk("c3", "doc-missing", "Orphan passage."),
    ]
    events, tool_result = _run_answer(chunks)

    assert "<source_documents>" in tool_result
    # Title, filename fallback, and Unknown fallback — exact legacy format
    assert "[EU AI Act Analysis, p.3, sec: Findings]\nFirst passage." in tool_result
    assert "[notes.txt, p.1, sec: Intro]\nSecond passage." in tool_result
    assert "[Unknown, p.3, sec: Findings]\nOrphan passage." in tool_result


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
    _run("no-results message", test_no_results_message)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
