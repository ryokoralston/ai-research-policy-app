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

T-12 added the three failure-path tests at the bottom: the write order is
SQLite (flushed, uncommitted) -> Chroma -> BM25 -> commit, and a failure
anywhere after the flush must leave BOTH search indexes with none of the
chunk ids, so rag/reconcile.py's "no index entry without a row" invariant
survives a failed index run.

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
    """Stateful enough to prove T-12's cleanup: `ids` is what the store would
    actually hold, so a test can assert the index is EMPTY after a failed
    index run rather than only that delete_by_ids happened to be called."""
    last_add = None
    last_delete = None
    ids: list[str] = []

    @classmethod
    def reset(cls):
        _FakeVectorStore.last_add = None
        _FakeVectorStore.last_delete = None
        _FakeVectorStore.ids = []

    def add_chunks(self, chunk_ids, embeddings, documents, metadatas):
        _FakeVectorStore.last_add = {
            "chunk_ids": chunk_ids,
            "embeddings": embeddings,
            "documents": documents,
            "metadatas": metadatas,
        }
        _FakeVectorStore.ids = _FakeVectorStore.ids + list(chunk_ids)

    def delete_by_ids(self, chunk_ids):
        _FakeVectorStore.last_delete = list(chunk_ids)
        gone = set(chunk_ids)
        _FakeVectorStore.ids = [i for i in _FakeVectorStore.ids if i not in gone]
        return len(chunk_ids)


class _ExplodingVectorStore(_FakeVectorStore):
    """Chroma itself fails — the BM25 write must never be attempted."""

    def add_chunks(self, chunk_ids, embeddings, documents, metadatas):
        raise RuntimeError("chroma unavailable")


class _FakeLexicalIndex:
    """Stands in for the real rag.lexical_index.LexicalIndex, which — unlike
    EmbeddingService/VectorStore above — was NOT previously faked in this
    file: _embed_and_store instantiated the real class, pointed at the real
    on-disk settings.bm25_index_path, so every run of this test module wrote
    live rows into the developer's real backend/data/bm25.db. Faking it here
    both fixes that leak and lets these tests assert on what _embed_and_store
    sends the lexical index (display/match/context split)."""
    last_add = None
    last_delete = None
    ids: list[str] = []

    @classmethod
    def reset(cls):
        _FakeLexicalIndex.last_add = None
        _FakeLexicalIndex.last_delete = None
        _FakeLexicalIndex.ids = []

    def add_chunks(self, chunk_ids, documents, metadatas, display_documents=None, contexts=None):
        _FakeLexicalIndex.last_add = {
            "chunk_ids": chunk_ids,
            "documents": documents,
            "metadatas": metadatas,
            "display_documents": display_documents,
            "contexts": contexts,
        }
        _FakeLexicalIndex.ids = _FakeLexicalIndex.ids + list(chunk_ids)

    def delete_by_chunk_ids(self, chunk_ids):
        _FakeLexicalIndex.last_delete = list(chunk_ids)
        gone = set(chunk_ids)
        _FakeLexicalIndex.ids = [i for i in _FakeLexicalIndex.ids if i not in gone]
        return len(chunk_ids)


class _ExplodingLexicalIndex(_FakeLexicalIndex):
    """BM25 fails AFTER Chroma has accepted the same ids — the asymmetric case
    T-12 exists for (see the audit: "単に2行入れ替えるだけでは Chroma成功・
    BM25失敗のケースで orphan が残る")."""

    def add_chunks(self, chunk_ids, documents, metadatas, display_documents=None, contexts=None):
        raise RuntimeError("bm25 index is locked")


async def _fake_contextualize_chunks_empty(full_text, chunk_texts, concurrency=4):
    """Default fake for rag_service.contextualize_chunks: Contextual
    Retrieval disabled/no-op, matching this file's pre-feature assertions
    (byte-identical embedding/lexical text). No live API call, no live DB —
    see rag/contextualizer.py's own flag-off contract."""
    return [""] * len(chunk_texts)


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


def _run_index(
    db,
    doc_id,
    embedding_cls=_FakeEmbeddingService,
    contextualize_fake=_fake_contextualize_chunks_empty,
    vector_store_cls=_FakeVectorStore,
    lexical_cls=_FakeLexicalIndex,
):
    orig_embed, orig_vs, orig_lex, orig_ctx = (
        rag_service.EmbeddingService, rag_service.VectorStore,
        rag_service.LexicalIndex, rag_service.contextualize_chunks,
    )
    rag_service.EmbeddingService = embedding_cls
    rag_service.VectorStore = vector_store_cls
    rag_service.LexicalIndex = lexical_cls
    rag_service.contextualize_chunks = contextualize_fake
    # Subclassed fakes deliberately share the base classes' state, so one
    # reset covers both the working and the exploding variants.
    _FakeVectorStore.reset()
    _FakeLexicalIndex.reset()
    try:
        return asyncio.run(rag_service.index_document(doc_id, db))
    finally:
        rag_service.EmbeddingService, rag_service.VectorStore = orig_embed, orig_vs
        rag_service.LexicalIndex, rag_service.contextualize_chunks = orig_lex, orig_ctx


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


def test_contextual_retrieval_integration_combines_text_preserves_display():
    """When contextualize_chunks returns non-empty contexts, embeddings/lexical
    match text must be combine(context, content), while Chroma's `documents`
    (citation/display contract) and the lexical index's `display_documents`
    stay the ORIGINAL chunk content — the AI-generated context must never
    leak into what's shown to users."""
    db = _make_db()
    path = _write_txt(_long_content())
    doc_id = _seed_doc(db, path)

    captured_full_text = {}

    async def fake_ctx(full_text, chunk_texts, concurrency=4):
        captured_full_text["value"] = full_text
        return [f"context-for-{i}" for i in range(len(chunk_texts))]

    try:
        _run_index(db, doc_id, contextualize_fake=fake_ctx)

        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )
        # full_text handed to contextualize_chunks is the join of already-
        # chunked content (see rag_service.index_document's full_text build).
        assert all(c.content in captured_full_text["value"] for c in chunks)

        vs_add = _FakeVectorStore.last_add
        lex_add = _FakeLexicalIndex.last_add
        expected_contexts = [f"context-for-{i}" for i in range(len(chunks))]

        # Chroma: documents = ORIGINAL content; metadatas carry the context.
        assert vs_add["documents"] == [c.content for c in chunks]
        assert [m["context"] for m in vs_add["metadatas"]] == expected_contexts

        # Lexical: match text (documents) = combine(context, content);
        # display_documents/contexts carry the original/context split.
        assert lex_add["documents"] == [
            f"{ctx}\n\n{c.content}" for ctx, c in zip(expected_contexts, chunks)
        ]
        assert lex_add["display_documents"] == [c.content for c in chunks]
        assert lex_add["contexts"] == expected_contexts

        # DocumentChunk.content in SQLite is untouched original text too.
        assert all(
            c.content == orig for c, orig in zip(chunks, [c.content for c in chunks])
        )
        db.close()
    finally:
        os.remove(path)


def test_contextual_retrieval_disabled_is_byte_identical_except_context_key():
    """Flag-off contract (see rag/contextualizer.py): every context is "",
    so embedded/matched text is unchanged from pre-feature behavior — only
    the harmless "context": "" metadata key differs."""
    db = _make_db()
    path = _write_txt(_long_content())
    doc_id = _seed_doc(db, path)
    try:
        _run_index(db, doc_id)  # default fake returns [""] * len

        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )
        vs_add = _FakeVectorStore.last_add
        lex_add = _FakeLexicalIndex.last_add

        assert vs_add["documents"] == [c.content for c in chunks]
        assert all(m["context"] == "" for m in vs_add["metadatas"])
        # combine("", content) == content unchanged
        assert lex_add["documents"] == [c.content for c in chunks]
        assert lex_add["display_documents"] == [c.content for c in chunks]
        assert lex_add["contexts"] == [""] * len(chunks)
        db.close()
    finally:
        os.remove(path)


def test_bm25_failure_removes_the_chunks_chroma_already_took():
    """T-12, the asymmetric case: Chroma accepted the ids, then BM25 raised.
    Without the cleanup those Chroma entries are orphans — searchable and
    citable with no document_chunks row behind them, exactly what
    rag/reconcile.py's invariant forbids."""
    db = _make_db()
    path = _write_txt(_long_content())
    doc_id = _seed_doc(db, path)
    try:
        raised = None
        try:
            _run_index(db, doc_id, lexical_cls=_ExplodingLexicalIndex)
        except Exception as exc:
            raised = exc
        assert isinstance(raised, RuntimeError), f"expected the original RuntimeError, got {raised!r}"
        assert "bm25" in str(raised), str(raised)

        written_ids = _FakeVectorStore.last_add["chunk_ids"]
        assert written_ids, "Chroma should have been written before BM25 failed"
        # Chroma cleaned by exact id (not delete_document by doc_id)...
        assert _FakeVectorStore.last_delete == written_ids
        assert _FakeVectorStore.ids == [], "no chunk ids may survive in Chroma"
        # ...and BM25 never held anything, so it must not have been touched.
        assert _FakeLexicalIndex.last_delete is None
        assert _FakeLexicalIndex.ids == []

        doc = db.query(Document).filter(Document.id == doc_id).first()
        chunk_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count()
        assert chunk_count == 0, f"the flushed rows must roll back, found {chunk_count}"
        assert doc.status == "error", doc.status
        db.close()
    finally:
        os.remove(path)


def test_chroma_failure_leaves_bm25_untouched():
    """The other half of the asymmetry: when Chroma is what raises, nothing
    reached either index, so BM25 must be neither written nor cleaned."""
    db = _make_db()
    path = _write_txt(_long_content())
    doc_id = _seed_doc(db, path)
    try:
        raised = None
        try:
            _run_index(db, doc_id, vector_store_cls=_ExplodingVectorStore)
        except Exception as exc:
            raised = exc
        assert isinstance(raised, RuntimeError), f"expected the original RuntimeError, got {raised!r}"
        assert "chroma" in str(raised), str(raised)

        assert _FakeLexicalIndex.last_add is None, "BM25 must not run after Chroma fails"
        assert _FakeLexicalIndex.last_delete is None, "nothing to clean in BM25"
        assert _FakeVectorStore.last_delete is None, "Chroma took nothing to clean"
        assert _FakeVectorStore.ids == []

        doc = db.query(Document).filter(Document.id == doc_id).first()
        chunk_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count()
        assert chunk_count == 0
        assert doc.status == "error", doc.status
        db.close()
    finally:
        os.remove(path)


def test_commit_failure_cleans_both_indexes():
    """The failure the audit actually names: both indexes accepted the chunks
    and the SQLite COMMIT is what fails. The flush cannot catch this one
    (lock/disk errors surface at commit), so the commit is inside the same
    guarded block and both stores get cleaned."""
    db = _make_db()
    path = _write_txt(_long_content())
    doc_id = _seed_doc(db, path)
    try:
        real_commit = db.commit
        state = {"failed": False}

        def commit_failing_once():
            # Only the indexing commit fails; the caller's own
            # status="error" commit must still go through.
            if not state["failed"]:
                state["failed"] = True
                raise RuntimeError("disk I/O error on commit")
            return real_commit()

        db.commit = commit_failing_once
        raised = None
        try:
            _run_index(db, doc_id)
        except Exception as exc:
            raised = exc
        db.commit = real_commit
        assert isinstance(raised, RuntimeError), f"expected the original RuntimeError, got {raised!r}"
        assert "commit" in str(raised), str(raised)

        written_ids = _FakeVectorStore.last_add["chunk_ids"]
        assert _FakeVectorStore.last_delete == written_ids
        assert _FakeLexicalIndex.last_delete == written_ids
        assert _FakeVectorStore.ids == []
        assert _FakeLexicalIndex.ids == []

        doc = db.query(Document).filter(Document.id == doc_id).first()
        chunk_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count()
        assert chunk_count == 0
        assert doc.status == "error", doc.status
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
    _run("contextual retrieval integration combines text, preserves display", test_contextual_retrieval_integration_combines_text_preserves_display)
    _run("contextual retrieval disabled is byte-identical except context key", test_contextual_retrieval_disabled_is_byte_identical_except_context_key)
    _run("bm25 failure removes the chunks chroma already took", test_bm25_failure_removes_the_chunks_chroma_already_took)
    _run("chroma failure leaves bm25 untouched", test_chroma_failure_leaves_bm25_untouched)
    _run("commit failure cleans both indexes", test_commit_failure_cleans_both_indexes)
    _run("missing document or file_path is a no-op", test_missing_document_or_file_path_is_a_noop)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
