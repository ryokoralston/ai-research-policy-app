"""Tests for services.rag_service.index_web_content (save-to-library indexing).

Save-to-library used to chunk with an ad-hoc ~800-char splitter duplicated in
routers/research.py; it now goes through the standard chunker
(chunk_plain_text) plus a single-chunk fallback for content shorter than the
chunker's minimum, so snippet-only sources remain searchable.

Covers:
  1. Long content: standard chunker output persisted to DB + Chroma with
     page/section/chunk_index metadata (needed by the retriever's reading order)
  2. Short content: single-chunk fallback, status='indexed'
  3. Empty content: zero chunks, status='indexed' (not an error — matches the
     original router behavior)
  4. Embedding failure: status='error', no chunks persisted

chromadb / sentence-transformers are stubbed; EmbeddingService and VectorStore
are replaced with fakes — no models, no network.

Run from the backend directory:
    ./venv/bin/python -m tests.test_index_web_content
"""
import asyncio
import os
import sys
import types
import uuid

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

# ── Stub heavy optional deps before importing the module under test ───────────
for _name in ("chromadb", "sentence_transformers"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models import Document, DocumentChunk
import services.rag_service as rag_service


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeEmbeddingService:
    def embed_texts(self, texts):
        return [[0.0, 1.0] for _ in texts]


class _BrokenEmbeddingService:
    def embed_texts(self, texts):
        raise RuntimeError("embedding model unavailable")


class _FakeVectorStore:
    last_add = None  # records the most recent add_chunks kwargs

    def add_chunks(self, chunk_ids, embeddings, documents, metadatas):
        _FakeVectorStore.last_add = {
            "chunk_ids": chunk_ids,
            "documents": documents,
            "metadatas": metadatas,
        }


def _make_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_doc(db) -> str:
    doc_id = str(uuid.uuid4())
    db.add(Document(
        id=doc_id,
        filename="source.web",
        title="Web Source",
        source_type="web",
        status="processing",
    ))
    db.commit()
    return doc_id


def _run_index(db, doc_id, content, embedding_cls=_FakeEmbeddingService):
    orig_embed, orig_vs = rag_service.EmbeddingService, rag_service.VectorStore
    rag_service.EmbeddingService = embedding_cls
    rag_service.VectorStore = _FakeVectorStore
    _FakeVectorStore.last_add = None
    try:
        asyncio.run(rag_service.index_web_content(doc_id, content, db))
    finally:
        rag_service.EmbeddingService, rag_service.VectorStore = orig_embed, orig_vs


def _long_content() -> str:
    """~900 words with paragraphs and a heading — enough for multiple chunks."""
    para = ("Artificial intelligence policy continues to evolve rapidly across "
            "jurisdictions with new legislative proposals every quarter. ") * 6
    return "INTRODUCTION\n" + "\n\n".join([para] * 12)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_long_content_uses_standard_chunker():
    db = _make_db()
    doc_id = _seed_doc(db)
    _run_index(db, doc_id, _long_content())

    doc = db.query(Document).filter(Document.id == doc_id).first()
    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == doc_id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )
    assert doc.status == "indexed", doc.status
    assert len(chunks) > 1, f"expected multiple chunks, got {len(chunks)}"
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    # Standard-chunker metadata that the old ad-hoc splitter never produced
    assert all(c.page_number == 1 for c in chunks)
    assert all(c.section_header for c in chunks), "section headers expected"
    # Chroma metadata must carry chunk_index (retriever reading order) and match DB
    metas = _FakeVectorStore.last_add["metadatas"]
    assert [m["chunk_index"] for m in metas] == [c.chunk_index for c in chunks]
    assert all(m["doc_id"] == doc_id for m in metas)
    assert doc.word_count and doc.word_count > 500
    db.close()


def test_short_content_falls_back_to_single_chunk():
    db = _make_db()
    doc_id = _seed_doc(db)
    short = "AI regulation summary: the bill passed committee review this week."
    _run_index(db, doc_id, short)

    doc = db.query(Document).filter(Document.id == doc_id).first()
    chunks = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).all()
    assert doc.status == "indexed", doc.status
    assert len(chunks) == 1, f"expected fallback single chunk, got {len(chunks)}"
    assert chunks[0].content == short
    assert _FakeVectorStore.last_add["documents"] == [short]
    db.close()


def test_empty_content_marks_indexed_without_chunks():
    db = _make_db()
    doc_id = _seed_doc(db)
    _run_index(db, doc_id, "   \n\n  ")

    doc = db.query(Document).filter(Document.id == doc_id).first()
    chunk_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count()
    assert doc.status == "indexed", doc.status
    assert chunk_count == 0, chunk_count
    assert _FakeVectorStore.last_add is None, "nothing should reach the vector store"
    db.close()


def test_embedding_failure_marks_error():
    db = _make_db()
    doc_id = _seed_doc(db)
    _run_index(db, doc_id, _long_content(), embedding_cls=_BrokenEmbeddingService)

    doc = db.query(Document).filter(Document.id == doc_id).first()
    chunk_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count()
    assert doc.status == "error", doc.status
    assert chunk_count == 0, chunk_count
    db.close()


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
    print("\nRunning index_web_content tests...\n")

    _run("long content uses standard chunker", test_long_content_uses_standard_chunker)
    _run("short content falls back to single chunk", test_short_content_falls_back_to_single_chunk)
    _run("empty content marks indexed without chunks", test_empty_content_marks_indexed_without_chunks)
    _run("embedding failure marks error", test_embedding_failure_marks_error)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
