"""Tests for services.rag_service.index_document (upload indexing path).

F-1 extracted a shared `_embed_and_store` helper used by both index_document
and index_web_content (previously ~50 near-identical lines in each). This
file pins index_document's own behavior — including the two ways it differs
from index_web_content, which the shared helper must NOT swallow:

  1. Empty/rejected chunker output is an ERROR here (unlike index_web_content,
     which marks "indexed" with zero chunks for save-to-library sources).
  2. On any exception, index_document marks status="error" AND re-raises
     (index_web_content swallows the exception since it runs as a
     fire-and-forget background task).

Also covers the normal .txt upload path end-to-end: chunk metadata persisted
to DB + Chroma, page_count/word_count set, status="indexed".

chromadb / sentence-transformers are stubbed; EmbeddingService and VectorStore
are replaced with fakes — no models, no network. Uses a real .txt file on disk
(chunk_plain_text has no heavy deps); .pdf/.html branches are unchanged code
paths not exercised here (chunk_pdf/chunk_html are covered by test_chunker.py).

Run from the backend directory:
    ./venv/bin/python -m tests.test_index_document
"""
import asyncio
import os
import sys
import tempfile
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
from models import Document, DocumentChunk
import services.rag_service as rag_service


class _FakeEmbeddingService:
    def embed_texts(self, texts):
        return [[0.0, 1.0] for _ in texts]


class _BrokenEmbeddingService:
    def embed_texts(self, texts):
        raise RuntimeError("embedding model unavailable")


class _FakeVectorStore:
    last_add = None

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


def _seed_doc(db, file_path) -> str:
    doc_id = str(uuid.uuid4())
    db.add(Document(
        id=doc_id,
        filename=os.path.basename(file_path),
        source_type="upload",
        file_path=file_path,
        status="processing",
    ))
    db.commit()
    return doc_id


def _write_txt(content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _run_index(db, doc_id, embedding_cls=_FakeEmbeddingService):
    orig_embed, orig_vs = rag_service.EmbeddingService, rag_service.VectorStore
    rag_service.EmbeddingService = embedding_cls
    rag_service.VectorStore = _FakeVectorStore
    _FakeVectorStore.last_add = None
    try:
        return asyncio.run(rag_service.index_document(doc_id, db))
    finally:
        rag_service.EmbeddingService, rag_service.VectorStore = orig_embed, orig_vs


def _long_content() -> str:
    para = ("Artificial intelligence policy continues to evolve rapidly across "
            "jurisdictions with new legislative proposals every quarter. ") * 6
    return "INTRODUCTION\n" + "\n\n".join([para] * 12)


def test_txt_upload_indexes_via_shared_helper():
    db = _make_db()
    path = _write_txt(_long_content())
    doc_id = _seed_doc(db, path)
    try:
        _run_index(db, doc_id)

        doc = db.query(Document).filter(Document.id == doc_id).first()
        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )
        assert doc.status == "indexed", doc.status
        assert len(chunks) > 1, f"expected multiple chunks, got {len(chunks)}"
        assert doc.page_count is None, "txt uploads have no page_count"
        assert doc.word_count and doc.word_count > 500
        assert doc.indexed_at is not None
        metas = _FakeVectorStore.last_add["metadatas"]
        assert [m["chunk_index"] for m in metas] == [c.chunk_index for c in chunks]
        assert all(m["doc_id"] == doc_id for m in metas)
        db.close()
    finally:
        os.remove(path)


def test_no_chunks_is_an_error_unlike_web_content():
    """Contrast with index_web_content: empty content here is an error, not
    silently marked 'indexed' with zero chunks."""
    db = _make_db()
    path = _write_txt("   \n\n  ")  # whitespace-only -> chunker returns no chunks
    doc_id = _seed_doc(db, path)
    try:
        _run_index(db, doc_id)
        doc = db.query(Document).filter(Document.id == doc_id).first()
        chunk_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count()
        assert doc.status == "error", doc.status
        assert chunk_count == 0
        db.close()
    finally:
        os.remove(path)


def test_embedding_failure_marks_error_and_reraises():
    """Contrast with index_web_content: index_document re-raises after marking
    status=error (the exception-handling policy the shared helper leaves to
    the caller)."""
    db = _make_db()
    path = _write_txt(_long_content())
    doc_id = _seed_doc(db, path)
    try:
        raised = False
        try:
            _run_index(db, doc_id, embedding_cls=_BrokenEmbeddingService)
        except RuntimeError:
            raised = True
        assert raised, "index_document must re-raise, unlike index_web_content"

        doc = db.query(Document).filter(Document.id == doc_id).first()
        chunk_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count()
        assert doc.status == "error", doc.status
        assert chunk_count == 0
        db.close()
    finally:
        os.remove(path)


def test_missing_document_or_file_path_is_a_noop():
    db = _make_db()
    # No document row at all
    asyncio.run(rag_service.index_document(str(uuid.uuid4()), db))  # must not raise

    # Document row exists but has no file_path
    doc_id = str(uuid.uuid4())
    db.add(Document(id=doc_id, filename="x.txt", source_type="upload", status="processing"))
    db.commit()
    asyncio.run(rag_service.index_document(doc_id, db))  # must not raise
    doc = db.query(Document).filter(Document.id == doc_id).first()
    assert doc.status == "processing", "untouched when there's no file to index"
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
    print("\nRunning index_document tests...\n")

    _run("txt upload indexes via shared helper", test_txt_upload_indexes_via_shared_helper)
    _run("no chunks is an error unlike web content", test_no_chunks_is_an_error_unlike_web_content)
    _run("embedding failure marks error and reraises", test_embedding_failure_marks_error_and_reraises)
    _run("missing document or file_path is a no-op", test_missing_document_or_file_path_is_a_noop)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
