"""Tests for per-user data scoping — the guarantee that one signed-in user can
never read, export, or delete another user's content.

Every content table (documents, reports, research_sessions, risk_analyses,
debates, reminders) carries a user_id, and every per-object endpoint filters on
it. The rules these tests pin down:

  - another user's row is a 404, never a 403 (a 403 would confirm the id exists)
  - a refused DELETE must leave the row intact
  - list endpoints return only the caller's own rows
  - admins get NO cross-user visibility: admin privilege covers user/audit
    administration, not other people's content
  - RAG retrieval for a user with no documents returns nothing, rather than
    falling through to an unfiltered search of everyone's library
  - the ownership migration is idempotent

Run from the backend directory:
    ./venv/bin/python -m tests.test_data_scoping
"""
import os
import sys
import tempfile
import types
import uuid
from datetime import datetime, timedelta

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# The migration test runs against database.engine itself, so it must point at a
# real file: an in-memory "sqlite://" URL without StaticPool hands every
# connection its own empty database, and the migration would appear to do
# nothing at all. Set before `database` is imported (its engine is built at
# import time). The router tests below use their own in-memory engines.
_MIGRATION_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="scoping-migration-"), "migration.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_MIGRATION_DB_PATH}"

from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())

# Heavy optional deps the documents router pulls in lazily — stubbed so this
# runner never loads chromadb / sentence-transformers.
for _name in ("chromadb", "sentence_transformers"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from database import Base, get_db
from models import Debate, Document, Report, ResearchSession, RiskAnalysis
from models.organization import Organization
from models.reminder import Reminder
from models.user import User
from services.auth import get_current_user
from services.user_service import ROLE_ADMIN, ROLE_MEMBER


# ── Harness ───────────────────────────────────────────────────────────────────

class _Harness:
    """One in-memory DB, all six content routers, and a switchable caller."""

    def __init__(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

        from routers import analysis, debate, documents, reminders, reports, research

        app = FastAPI()
        for module in (analysis, debate, documents, reminders, reports, research):
            app.include_router(module.router)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.acting

        self.alice = self._user("alice@example.com", ROLE_MEMBER)
        self.bob = self._user("bob@example.com", ROLE_MEMBER)
        self.admin = self._user("admin@example.com", ROLE_ADMIN)
        self.acting = self.alice
        self.client = TestClient(app)

    def _user(self, email: str, role: str) -> User:
        org = Organization(id=str(uuid.uuid4()), name=email)
        self.db.add(org)
        user = User(
            id=str(uuid.uuid4()), email=email, password_hash="x", role=role, org_id=org.id
        )
        self.db.add(user)
        self.db.commit()
        return user

    def act_as(self, user: User) -> None:
        self.acting = user

    def add(self, row):
        self.db.add(row)
        self.db.commit()
        return row

    def still_exists(self, model, row_id: str) -> bool:
        self.db.expire_all()
        return self.db.query(model).filter(model.id == row_id).first() is not None


def _owned_by(user: User, **kwargs) -> dict:
    return {"user_id": user.id, "org_id": user.org_id, **kwargs}


# Every resource as (label, model, factory, url_prefix, has_export). The
# factory builds a row owned by the given user.
_RESOURCES = [
    (
        "documents", Document,
        lambda u: Document(id=str(uuid.uuid4()), filename="secret.pdf", title="Secret",
                           source_type="upload", status="indexed", **_owned_by(u)),
        "/api/documents", False,
    ),
    (
        "reports", Report,
        lambda u: Report(id=str(uuid.uuid4()), title="Secret Report",
                         report_type="policy_memo", status="draft", content="classified",
                         **_owned_by(u)),
        "/api/reports", True,
    ),
    (
        "research_sessions", ResearchSession,
        lambda u: ResearchSession(id=str(uuid.uuid4()), query="secret query",
                                  status="complete", **_owned_by(u)),
        "/api/research", False,
    ),
    (
        "risk_analyses", RiskAnalysis,
        lambda u: RiskAnalysis(id=str(uuid.uuid4()), subject="Secret Subject",
                               analysis_type="technology", content="classified",
                               **_owned_by(u)),
        "/api/analysis", True,
    ),
    (
        "debates", Debate,
        lambda u: Debate(id=str(uuid.uuid4()), topic="secret topic", status="complete",
                         **_owned_by(u)),
        "/api/debate", False,
    ),
    (
        "reminders", Reminder,
        lambda u: Reminder(id=str(uuid.uuid4()), content="secret reminder",
                           due_at=datetime.utcnow() + timedelta(days=1), **_owned_by(u)),
        "/api/reminders", False,
    ),
]

# Reminders expose no per-id GET — only list and delete.
_NO_GET = {"reminders"}


# ── Cross-user access ─────────────────────────────────────────────────────────

def _assert_cannot_reach(h: _Harness, intruder: User, label, model, factory, prefix, has_export):
    h.act_as(h.alice)
    row = h.add(factory(h.alice))

    h.act_as(intruder)

    if label not in _NO_GET:
        resp = h.client.get(f"{prefix}/{row.id}")
        assert resp.status_code == 404, f"{label} GET: expected 404, got {resp.status_code}"

    if has_export:
        resp = h.client.get(f"{prefix}/{row.id}/export?format=txt")
        assert resp.status_code == 404, f"{label} export: expected 404, got {resp.status_code}"

    resp = h.client.delete(f"{prefix}/{row.id}")
    assert resp.status_code == 404, f"{label} DELETE: expected 404, got {resp.status_code}"
    assert h.still_exists(model, row.id), f"{label}: refused DELETE destroyed the row anyway"

    listed = h.client.get(f"{prefix}/")
    assert listed.status_code == 200, f"{label} list: {listed.status_code} {listed.text}"
    assert row.id not in listed.text, f"{label}: another user's row appeared in the list"

    # The owner is unaffected by all of the above.
    h.act_as(h.alice)
    if label not in _NO_GET:
        resp = h.client.get(f"{prefix}/{row.id}")
        assert resp.status_code == 200, f"{label}: owner lost access ({resp.status_code})"
    assert row.id in h.client.get(f"{prefix}/").text, f"{label}: owner's row missing from list"


def test_member_cannot_reach_another_members_rows():
    for label, model, factory, prefix, has_export in _RESOURCES:
        h = _Harness()
        _assert_cannot_reach(h, h.bob, label, model, factory, prefix, has_export)


def test_admin_cannot_reach_a_members_rows():
    """Decision D2: filtering is user_id == caller for everyone, admins included.
    Admin privilege stays limited to the admin-only routers."""
    for label, model, factory, prefix, has_export in _RESOURCES:
        h = _Harness()
        _assert_cannot_reach(h, h.admin, label, model, factory, prefix, has_export)


def test_streams_are_scoped():
    """The SSE stream endpoints resolve a row by id too — same 404 rule."""
    h = _Harness()
    h.act_as(h.alice)
    session = h.add(ResearchSession(id=str(uuid.uuid4()), query="q", status="running",
                                     **_owned_by(h.alice)))
    debate = h.add(Debate(id=str(uuid.uuid4()), topic="t", status="running",
                          **_owned_by(h.alice)))

    h.act_as(h.bob)
    resp = h.client.get(f"/api/research/{session.id}/stream")
    assert resp.status_code == 404, resp.status_code
    resp = h.client.get(f"/api/debate/{debate.id}/stream")
    assert resp.status_code == 404, resp.status_code


def test_report_patch_is_scoped():
    """The write path is scoped like the read paths — a 404, and no mutation."""
    h = _Harness()
    h.act_as(h.alice)
    report = h.add(Report(id=str(uuid.uuid4()), title="Alice's report",
                          report_type="policy_memo", status="draft", **_owned_by(h.alice)))

    h.act_as(h.bob)
    resp = h.client.patch(f"/api/reports/{report.id}", json={"title": "Bob was here"})
    assert resp.status_code == 404, resp.status_code

    h.db.expire_all()
    fresh = h.db.query(Report).filter(Report.id == report.id).first()
    assert fresh.title == "Alice's report", fresh.title


def test_report_generate_rejects_another_users_session():
    """session_id is persisted on the report row, so it is validated at
    creation: a report must never attach itself to someone else's session."""
    h = _Harness()
    h.act_as(h.alice)
    session = h.add(ResearchSession(id=str(uuid.uuid4()), query="alice's research",
                                     status="complete", summary="secret synthesis",
                                     **_owned_by(h.alice)))

    h.act_as(h.bob)
    resp = h.client.post("/api/reports/generate", json={
        "report_type": "policy_memo", "title": "Borrowed", "session_id": session.id,
    })
    assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text}"
    assert h.db.query(Report).count() == 0, "a report row was created for a foreign session"


def test_document_folder_writes_are_scoped():
    """assign-folder / rename-folder take ids from the request body — they must
    skip documents the caller does not own instead of rewriting them."""
    h = _Harness()
    h.act_as(h.alice)
    doc = h.add(Document(id=str(uuid.uuid4()), filename="a.txt", source_type="upload",
                         status="indexed", metadata_json='{"collection_id": "f1", '
                                                          '"collection_name": "Alice folder"}',
                         **_owned_by(h.alice)))

    h.act_as(h.bob)
    resp = h.client.post("/api/documents/assign-folder",
                         json={"doc_ids": [doc.id], "folder_id": "f9", "folder_name": "Bob"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 0, resp.text

    resp = h.client.post("/api/documents/rename-folder",
                         json={"folder_id": "f1", "new_name": "Bob renamed it"})
    assert resp.json()["updated"] == 0, resp.text

    h.db.expire_all()
    fresh = h.db.query(Document).filter(Document.id == doc.id).first()
    assert "Alice folder" in (fresh.metadata_json or ""), fresh.metadata_json


# ── RAG scoping ───────────────────────────────────────────────────────────────

def test_owned_doc_ids_excludes_other_users_documents():
    from routers.documents import _owned_doc_ids

    h = _Harness()
    alice_doc = h.add(Document(id=str(uuid.uuid4()), filename="a.txt", source_type="upload",
                               status="indexed", **_owned_by(h.alice)))

    # Bob owns nothing: an empty list, NOT None ("search everything").
    assert _owned_doc_ids(h.bob, h.db, None) == [], "a user with no documents must get []"
    # Naming Alice's document explicitly does not get Bob access to it.
    assert _owned_doc_ids(h.bob, h.db, [alice_doc.id]) == []
    assert _owned_doc_ids(h.alice, h.db, None) == [alice_doc.id]


def test_retrieval_with_no_documents_returns_nothing():
    """The zero-documents case must return no chunks — an empty doc_ids list
    must never fall through to an unfiltered search of the whole index."""
    from rag.retriever import Retriever

    class _NeverSearched(Retriever):
        def __init__(self):  # no embedding model / vector store construction
            self._vs = None
            self._embed = None
            self._lexical = None

    # Every backing store is None: if retrieve() searched anything at all this
    # would raise AttributeError rather than return.
    assert _NeverSearched().retrieve("anything", top_k=5, doc_ids=[]) == []


def test_workspace_roots_are_per_user():
    from services.text_editor_tool import WORKSPACE_DIR, resolve_workspace_path, user_workspace_dir

    alice_root = user_workspace_dir("alice-id")
    bob_root = user_workspace_dir("bob-id")
    assert alice_root != bob_root
    assert os.path.dirname(alice_root) == WORKSPACE_DIR

    # The containment check still rejects an escape attempt into the sibling
    # user's directory.
    try:
        resolve_workspace_path("../bob-id/notes.md", alice_root)
        raise AssertionError("expected a path escaping the user's workspace to be rejected")
    except ValueError:
        pass

    resolved = resolve_workspace_path("notes.md", alice_root)
    assert str(resolved).startswith(os.path.realpath(alice_root)), resolved


# ── Migration ─────────────────────────────────────────────────────────────────

def _migration_state(conn) -> tuple:
    orgs = conn.execute(text("SELECT id, name FROM organizations ORDER BY name")).fetchall()
    users = conn.execute(text("SELECT id, org_id FROM users ORDER BY email")).fetchall()
    rows = []
    for table in database.OWNERSHIP_TABLES:
        rows += conn.execute(
            text(f"SELECT '{table}', id, user_id, org_id FROM {table} ORDER BY id")
        ).fetchall()
    return orgs, users, rows


def test_migration_is_idempotent():
    """Run the ownership migration twice against a real file-backed database:
    the second run must change nothing."""
    Base.metadata.create_all(database.engine)
    with database.engine.begin() as conn:
        conn.execute(text("DELETE FROM users"))
        conn.execute(text("DELETE FROM organizations"))
        for table in database.OWNERSHIP_TABLES:
            conn.execute(text(f"DELETE FROM {table}"))
        # Two admins (the OLDER one owns the backfill — decision D1) and a member.
        conn.execute(text(
            "INSERT INTO users (id, email, password_hash, role, is_active, created_at) VALUES "
            "('old-admin', 'old@example.com', 'x', 'admin', 1, '2020-01-01 00:00:00'),"
            "('new-admin', 'new@example.com', 'x', 'admin', 1, '2024-01-01 00:00:00'),"
            "('member', 'member@example.com', 'x', 'member', 1, '2025-01-01 00:00:00')"
        ))
        conn.execute(text(
            "INSERT INTO reports (id, title, report_type, status, created_at, updated_at) "
            "VALUES ('legacy-report', 'Legacy', 'policy_memo', 'draft', "
            "'2021-01-01 00:00:00', '2021-01-01 00:00:00')"
        ))
        conn.execute(text(
            "INSERT INTO documents (id, filename, source_type, status, created_at) "
            "VALUES ('legacy-doc', 'legacy.pdf', 'upload', 'indexed', '2021-01-01 00:00:00')"
        ))

    database.add_ownership_columns()
    with database.engine.begin() as conn:
        first = _migration_state(conn)

    orgs, users, rows = first
    assert len(orgs) == 3, orgs                      # one organization per user
    assert all(u[1] for u in users), users           # every user has one
    owner_org = dict(users)["old-admin"]
    for table, row_id, user_id, org_id in rows:
        assert user_id == "old-admin", (table, row_id, user_id)
        assert org_id == owner_org, (table, row_id, org_id)

    database.add_ownership_columns()
    with database.engine.begin() as conn:
        second = _migration_state(conn)

    assert first == second, "second migration run changed state"


def test_migration_no_op_without_users():
    """A fresh deploy (no users at all) must not crash init_db, and must not
    invent an owner."""
    engine = create_engine(
        f"sqlite:///{os.path.join(tempfile.mkdtemp(prefix='scoping-fresh-'), 'fresh.db')}"
    )
    original = database.engine
    database.engine = engine
    try:
        Base.metadata.create_all(engine)
        database.add_ownership_columns()  # must not raise
        with engine.begin() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM organizations")).scalar() == 0
            assert conn.execute(text("SELECT COUNT(*) FROM reports")).scalar() == 0
    finally:
        database.engine = original


def test_create_user_creates_an_organization():
    """New accounts must get an org at creation time, or every row they write
    afterwards would carry a NULL org_id."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    from services.user_service import create_user

    user = create_user(db, "fresh@example.com", "correct-horse-battery-staple")
    assert user.org_id, "create_user must assign an organization"
    org = db.query(Organization).filter(Organization.id == user.org_id).first()
    assert org is not None and org.name == "fresh@example.com", org


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
    print("\nRunning data scoping tests...\n")

    _run("member cannot read/export/delete another member's rows",
         test_member_cannot_reach_another_members_rows)
    _run("admin cannot read/export/delete a member's rows (D2)",
         test_admin_cannot_reach_a_members_rows)
    _run("SSE stream endpoints are scoped", test_streams_are_scoped)
    _run("report PATCH is scoped", test_report_patch_is_scoped)
    _run("report generate rejects another user's session_id",
         test_report_generate_rejects_another_users_session)
    _run("document folder writes are scoped", test_document_folder_writes_are_scoped)
    _run("owned doc_ids exclude other users' documents",
         test_owned_doc_ids_excludes_other_users_documents)
    _run("retrieval with no documents returns nothing",
         test_retrieval_with_no_documents_returns_nothing)
    _run("workspace roots are per user", test_workspace_roots_are_per_user)
    _run("ownership migration is idempotent", test_migration_is_idempotent)
    _run("ownership migration no-ops without users", test_migration_no_op_without_users)
    _run("create_user creates an organization", test_create_user_creates_an_organization)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
