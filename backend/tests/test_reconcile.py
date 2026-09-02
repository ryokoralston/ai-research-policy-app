"""Tests for rag/reconcile.py and the delete-ordering guarantee behind it.

The invariant under test: no ChromaDB entry and no BM25 entry may exist
without a backing `document_chunks` row. Two halves —

  - rag/reconcile.py detects entries that have lost their row (orphans) and
    never flags an entry that still has one, including the chroma_id case
    where the index key is not the chunk's primary key.
  - routers/documents.py's delete_document keeps the DB rows and returns 503
    when index cleanup fails, since rows that still exist are exactly what
    stops their index entries from becoming orphans.

Isolation: the BM25 side runs against a real LexicalIndex on a temp-file path
(same pattern as tests/test_lexical_index.py), chunk rows live in an in-memory
SQLAlchemy DB, and the vector store is a small fake implementing the three
methods reconcile uses. Nothing here touches the real dev database, the real
Chroma directory, or the real BM25 index — the real Chroma read path is
covered by running scripts/reconcile_indexes.py in dry-run against dev data.

Run from the backend directory:
    ./venv/bin/python -m tests.test_reconcile
"""
import os
import sys
import tempfile
import types
import uuid

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

# chromadb stub WITHOUT PersistentClient → VectorStore() raises inside the
# delete endpoint's try block, which is precisely the failure the 503 path
# exists for. sentence_transformers is stubbed so this runner never loads torch.
for _name in ("chromadb", "sentence_transformers"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from models import Document, DocumentChunk
from models.user import User
from rag.lexical_index import LexicalIndex
from rag.reconcile import delete_orphans, find_orphans
from services.auth import get_current_user

_FAKE_USER = User(id="test-user", email="user@example.com", password_hash="x", role="admin")


# ── Fixtures ──────────────────────────────────────────────────────────────────

class FakeVectorStore:
    """Stands in for VectorStore with only what reconcile.py calls.

    Not a mock of Chroma — just an id set with the same three-method surface
    (list_ids / delete_by_ids / count), so the reconcile logic is tested
    without a Chroma persist directory.
    """

    def __init__(self, ids):
        self.ids = list(ids)

    def list_ids(self):
        return list(self.ids)

    def delete_by_ids(self, chunk_ids):
        removed = 0
        for chunk_id in chunk_ids:
            while chunk_id in self.ids:
                self.ids.remove(chunk_id)
                removed += 1
        return removed

    def count(self):
        return len(self.ids)


def _tmp_index_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="reconcile_test_")
    os.close(fd)
    os.remove(path)  # LexicalIndex creates the file itself on first connect
    return path


def _mem_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _add_chunk(db, doc_id="doc-1", chunk_index=0, chroma_id=None) -> str:
    """Insert a chunk row and return the id the indexes would be keyed by."""
    chunk = DocumentChunk(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        chunk_index=chunk_index,
        content="text",
        chroma_id=chroma_id,
    )
    db.add(chunk)
    db.commit()
    return chunk.chroma_id or chunk.id


def _bm25_add(idx: LexicalIndex, chunk_id: str, doc_id: str = "doc-1") -> None:
    idx.add_chunks(
        chunk_ids=[chunk_id],
        documents=["some indexed text"],
        metadatas=[{"doc_id": doc_id, "page_number": 1, "section_header": "", "chunk_index": 0}],
    )


# ── find_orphans ──────────────────────────────────────────────────────────────

def test_find_orphans_flags_entry_with_no_chunk_row():
    db = _mem_db()
    path = _tmp_index_path()
    try:
        live_id = _add_chunk(db)
        orphan_id = "orphan-chunk-id"

        idx = LexicalIndex(db_path=path)
        _bm25_add(idx, live_id)
        _bm25_add(idx, orphan_id)
        vs = FakeVectorStore([live_id, orphan_id])

        report = find_orphans(db, vector_store=vs, lexical_index=idx)

        assert report["chroma_orphans"] == [orphan_id], report
        assert report["bm25_orphans"] == [orphan_id], report
        assert report["chroma_total"] == 2, report
        assert report["bm25_total"] == 2, report
        assert report["sqlite_chunk_ids"] == 1, report
    finally:
        db.close()
        os.path.exists(path) and os.remove(path)


def test_find_orphans_does_not_flag_legitimate_entry():
    db = _mem_db()
    path = _tmp_index_path()
    try:
        ids = [_add_chunk(db, chunk_index=i) for i in range(3)]

        idx = LexicalIndex(db_path=path)
        for chunk_id in ids:
            _bm25_add(idx, chunk_id)
        vs = FakeVectorStore(ids)

        report = find_orphans(db, vector_store=vs, lexical_index=idx)

        assert report["chroma_orphans"] == [], report
        assert report["bm25_orphans"] == [], report
        assert report["sqlite_chunk_ids"] == 3, report
    finally:
        db.close()
        os.path.exists(path) and os.remove(path)


def test_find_orphans_uses_chroma_id_when_present():
    """The index key is COALESCE(chroma_id, id) — a chunk whose chroma_id is
    set must be matched on that, not on its primary key. Getting this backwards
    would report every such entry as an orphan and, under --apply, delete it."""
    db = _mem_db()
    path = _tmp_index_path()
    try:
        indexed_id = _add_chunk(db, chroma_id="explicit-chroma-id")
        assert indexed_id == "explicit-chroma-id"

        idx = LexicalIndex(db_path=path)
        _bm25_add(idx, indexed_id)
        vs = FakeVectorStore([indexed_id])

        report = find_orphans(db, vector_store=vs, lexical_index=idx)
        assert report["chroma_orphans"] == [], report
        assert report["bm25_orphans"] == [], report
    finally:
        db.close()
        os.path.exists(path) and os.remove(path)


def test_find_orphans_is_read_only():
    """Nothing may be removed by a check that only reports."""
    db = _mem_db()
    path = _tmp_index_path()
    try:
        live_id = _add_chunk(db)
        idx = LexicalIndex(db_path=path)
        _bm25_add(idx, live_id)
        _bm25_add(idx, "orphan-a")
        vs = FakeVectorStore([live_id, "orphan-a"])

        find_orphans(db, vector_store=vs, lexical_index=idx)

        assert idx.count() == 2, "BM25 index was modified by a read-only check"
        assert vs.count() == 2, "vector store was modified by a read-only check"
        assert db.query(DocumentChunk).count() == 1, "DB was modified by a read-only check"
    finally:
        db.close()
        os.path.exists(path) and os.remove(path)


# ── delete_orphans ────────────────────────────────────────────────────────────

def test_delete_orphans_removes_exactly_the_orphans():
    db = _mem_db()
    path = _tmp_index_path()
    try:
        live_id = _add_chunk(db)
        idx = LexicalIndex(db_path=path)
        _bm25_add(idx, live_id)
        _bm25_add(idx, "orphan-a")
        _bm25_add(idx, "orphan-b")
        vs = FakeVectorStore([live_id, "orphan-a", "orphan-b"])

        result = delete_orphans(db, vector_store=vs, lexical_index=idx)

        assert result["chroma_removed"] == 2, result
        assert result["bm25_removed"] == 2, result
        assert vs.list_ids() == [live_id], vs.list_ids()
        assert idx.list_chunk_ids() == [live_id], idx.list_chunk_ids()

        after = find_orphans(db, vector_store=vs, lexical_index=idx)
        assert after["chroma_orphans"] == [] and after["bm25_orphans"] == [], after
    finally:
        db.close()
        os.path.exists(path) and os.remove(path)


def test_delete_orphans_no_op_when_clean():
    db = _mem_db()
    path = _tmp_index_path()
    try:
        live_id = _add_chunk(db)
        idx = LexicalIndex(db_path=path)
        _bm25_add(idx, live_id)
        vs = FakeVectorStore([live_id])

        result = delete_orphans(db, vector_store=vs, lexical_index=idx)

        assert result["chroma_removed"] == 0, result
        assert result["bm25_removed"] == 0, result
        assert idx.count() == 1 and vs.count() == 1, (idx.count(), vs.count())
    finally:
        db.close()
        os.path.exists(path) and os.remove(path)


def test_delete_orphans_removes_duplicate_bm25_rows():
    """FTS5 has no unique key, so one chunk_id can occupy several rows. All of
    them must go, and the reported count is rows removed, not ids."""
    db = _mem_db()
    path = _tmp_index_path()
    try:
        _add_chunk(db)
        idx = LexicalIndex(db_path=path)
        _bm25_add(idx, "orphan-dup")
        _bm25_add(idx, "orphan-dup")
        vs = FakeVectorStore([])

        result = delete_orphans(db, vector_store=vs, lexical_index=idx)

        assert result["bm25_removed"] == 2, result
        assert idx.count() == 0, idx.count()
    finally:
        db.close()
        os.path.exists(path) and os.remove(path)


# ── LexicalIndex primitives ───────────────────────────────────────────────────

def test_delete_by_chunk_ids_leaves_other_ids_alone():
    path = _tmp_index_path()
    try:
        idx = LexicalIndex(db_path=path)
        _bm25_add(idx, "keep-1")
        _bm25_add(idx, "drop-1")
        _bm25_add(idx, "keep-2")

        removed = idx.delete_by_chunk_ids(["drop-1"])
        assert removed == 1, removed
        assert sorted(idx.list_chunk_ids()) == ["keep-1", "keep-2"], idx.list_chunk_ids()

        assert idx.delete_by_chunk_ids([]) == 0
        assert idx.count() == 2, idx.count()
    finally:
        os.path.exists(path) and os.remove(path)


def test_delete_by_chunk_ids_batches_past_sqlite_variable_limit():
    """More ids than SQLite will bind to one statement must still delete
    cleanly rather than raising "too many SQL variables"."""
    path = _tmp_index_path()
    try:
        idx = LexicalIndex(db_path=path)
        ids = [f"bulk-{i}" for i in range(1200)]
        idx.add_chunks(
            chunk_ids=ids,
            documents=["text"] * len(ids),
            metadatas=[{"doc_id": "doc-1", "page_number": 1,
                        "section_header": "", "chunk_index": i} for i in range(len(ids))],
        )
        _bm25_add(idx, "survivor")

        removed = idx.delete_by_chunk_ids(ids)
        assert removed == 1200, removed
        assert idx.list_chunk_ids() == ["survivor"], idx.list_chunk_ids()
    finally:
        os.path.exists(path) and os.remove(path)


# ── delete_document ordering ──────────────────────────────────────────────────

def _make_client_and_db():
    db = _mem_db()
    from routers.documents import router as documents_router

    app = FastAPI()
    app.include_router(documents_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    return TestClient(app), db


def _indexed_doc(db) -> str:
    doc_id = str(uuid.uuid4())
    db.add(Document(id=doc_id, user_id=_FAKE_USER.id, filename="paper.pdf",
                    title="Paper", source_type="upload", status="indexed"))
    db.add(DocumentChunk(id=str(uuid.uuid4()), document_id=doc_id,
                         chunk_index=0, content="chunk text"))
    db.commit()
    return doc_id


def test_delete_document_keeps_rows_and_503s_when_vector_cleanup_fails():
    """The chromadb stub has no PersistentClient, so VectorStore() raises —
    the same shape as a real Chroma outage. The rows must survive: an index
    entry that still has its chunk row is not an orphan, so keeping the rows
    is what preserves the invariant until the caller retries."""
    client, db = _make_client_and_db()
    try:
        doc_id = _indexed_doc(db)

        resp = client.delete(f"/api/documents/{doc_id}")

        assert resp.status_code == 503, (resp.status_code, resp.text)
        assert "not deleted" in resp.json()["detail"], resp.text
        db.expire_all()
        assert db.query(Document).filter(Document.id == doc_id).first() is not None, (
            "document row was deleted despite failed index cleanup"
        )
        assert db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count() == 1, (
            "chunk rows were deleted despite failed index cleanup — this is exactly "
            "how orphans were created"
        )
    finally:
        db.close()


def test_delete_document_logs_no_audit_entry_for_a_delete_that_did_not_happen():
    """The audit log must not claim a document was deleted when it wasn't."""
    from models.audit_log import AuditLogEntry

    client, db = _make_client_and_db()
    try:
        doc_id = _indexed_doc(db)
        client.delete(f"/api/documents/{doc_id}")

        db.expire_all()
        entries = (
            db.query(AuditLogEntry)
            .filter(AuditLogEntry.action == "document.delete")
            .all()
        )
        assert entries == [], f"audit log recorded a delete that was refused: {entries}"
    finally:
        db.close()


def test_delete_document_missing_doc_still_404s_before_any_cleanup():
    """Ownership/existence checks run before the new cleanup block, so an
    unknown id must not surface as a 503 from the index path."""
    client, db = _make_client_and_db()
    try:
        resp = client.delete(f"/api/documents/{uuid.uuid4()}")
        assert resp.status_code == 404, (resp.status_code, resp.text)
    finally:
        db.close()


# ── Startup check must not load the embedding model ───────────────────────────

def test_reconcile_module_does_not_import_torch():
    """main.py runs find_orphans at startup. If importing reconcile (or its
    VectorStore construction path) ever pulled in sentence-transformers, every
    boot would pay for loading torch. sentence_transformers is stubbed at the
    top of this file, so this asserts on torch itself."""
    assert "torch" not in sys.modules, (
        "importing rag.reconcile pulled in torch — keep the embedding model "
        "lazy in services/embedding_service._load_model"
    )


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
    print("\nRunning index reconciliation tests...\n")

    _run("find_orphans flags entry with no chunk row", test_find_orphans_flags_entry_with_no_chunk_row)
    _run("find_orphans does not flag legitimate entry", test_find_orphans_does_not_flag_legitimate_entry)
    _run("find_orphans uses chroma_id when present", test_find_orphans_uses_chroma_id_when_present)
    _run("find_orphans is read-only", test_find_orphans_is_read_only)
    _run("delete_orphans removes exactly the orphans", test_delete_orphans_removes_exactly_the_orphans)
    _run("delete_orphans no-op when clean", test_delete_orphans_no_op_when_clean)
    _run("delete_orphans removes duplicate BM25 rows", test_delete_orphans_removes_duplicate_bm25_rows)
    _run("delete_by_chunk_ids leaves other ids alone", test_delete_by_chunk_ids_leaves_other_ids_alone)
    _run("delete_by_chunk_ids batches past SQLite variable limit", test_delete_by_chunk_ids_batches_past_sqlite_variable_limit)
    _run("delete_document keeps rows and 503s on vector cleanup failure", test_delete_document_keeps_rows_and_503s_when_vector_cleanup_fails)
    _run("delete_document logs no audit entry for refused delete", test_delete_document_logs_no_audit_entry_for_a_delete_that_did_not_happen)
    _run("delete_document unknown id still 404s", test_delete_document_missing_doc_still_404s_before_any_cleanup)
    _run("reconcile module does not import torch", test_reconcile_module_does_not_import_torch)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
