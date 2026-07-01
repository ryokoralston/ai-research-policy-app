"""Tests for document folder operations and swallowed-exception logging (E-4).

Exercises the previously-silent except branches: malformed metadata_json
during folder rename, ChromaDB cleanup failure during delete, and a failing
scheduler in reschedule_digest — all must log a warning and keep going.

Run from the backend directory:
    ./venv/bin/python -m tests.test_folder_ops
"""
import json
import os
import sys
import types
import uuid

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

# chromadb stub WITHOUT PersistentClient → VectorStore() raises inside the
# delete endpoint's try block, exercising the logged best-effort path.
for _name in ("chromadb", "sentence_transformers"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from models import Document


def _make_client_and_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    from routers.documents import router as documents_router
    app = FastAPI()
    app.include_router(documents_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app), db


def _doc(db, metadata_json=None):
    doc_id = str(uuid.uuid4())
    db.add(Document(id=doc_id, filename="a.txt", source_type="upload",
                    status="indexed", metadata_json=metadata_json))
    db.commit()
    return doc_id


def test_assign_and_rename_folder():
    client, db = _make_client_and_db()
    d1 = _doc(db)
    d2 = _doc(db)

    resp = client.post("/api/documents/assign-folder",
                       json={"doc_ids": [d1, d2], "folder_id": "f1", "folder_name": "Old"})
    assert resp.json()["updated"] == 2, resp.text

    resp = client.post("/api/documents/rename-folder",
                       json={"folder_id": "f1", "new_name": "New"})
    assert resp.json()["updated"] == 2, resp.text

    doc = db.query(Document).filter(Document.id == d1).first()
    db.refresh(doc)
    assert json.loads(doc.metadata_json)["collection_name"] == "New"
    db.close()


def test_rename_skips_malformed_metadata_and_logs():
    client, db = _make_client_and_db()
    good = _doc(db, metadata_json=json.dumps({"collection_id": "f1", "collection_name": "Old"}))
    bad = _doc(db, metadata_json="{not valid json")

    resp = client.post("/api/documents/rename-folder",
                       json={"folder_id": "f1", "new_name": "New"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 1, "only the well-formed doc is updated"

    doc = db.query(Document).filter(Document.id == bad).first()
    db.refresh(doc)
    assert doc.metadata_json == "{not valid json", "malformed row must be untouched"
    db.close()


def test_delete_survives_chroma_failure():
    client, db = _make_client_and_db()
    doc_id = _doc(db)

    # chromadb stub has no PersistentClient → VectorStore() raises; the
    # endpoint must log and still delete the DB row.
    resp = client.delete(f"/api/documents/{doc_id}")
    assert resp.status_code == 200, resp.text
    assert db.query(Document).filter(Document.id == doc_id).first() is None
    db.close()


def test_reschedule_digest_survives_scheduler_failure():
    import routers.digest as digest_router

    class BrokenScheduler:
        def reschedule_job(self, *a, **k):
            raise RuntimeError("no such job")

    original = digest_router._scheduler
    digest_router._scheduler = BrokenScheduler()
    try:
        digest_router.reschedule_digest(6, "America/New_York")  # must not raise
    finally:
        digest_router._scheduler = original


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
    print("\nRunning folder ops / logging tests...\n")

    _run("assign and rename folder", test_assign_and_rename_folder)
    _run("rename skips malformed metadata and logs", test_rename_skips_malformed_metadata_and_logs)
    _run("delete survives Chroma failure", test_delete_survives_chroma_failure)
    _run("reschedule_digest survives scheduler failure", test_reschedule_digest_survives_scheduler_failure)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
