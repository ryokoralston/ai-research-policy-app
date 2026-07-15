"""Tests for services/audit_log.py and routers/audit_log.py, plus the
instrumented mutation points (login, settings, document delete).

Run from the backend directory:
    ./venv/bin/python -m tests.test_audit_log
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from models.audit_log import AuditLogEntry
from models.user import User
from services import audit_log
from services.auth import get_current_user, require_admin
from services.user_service import ROLE_ADMIN, create_user


def _make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# ── services/audit_log.record ─────────────────────────────────────────────────

def test_record_stages_row_without_committing():
    db = _make_db()
    user = create_user(db, "recorder@example.com", "hunter2hunter2")
    audit_log.record(db, user=user, action="test.action", resource_type="thing", resource_id="1",
                      detail="did a thing", ip_address="10.0.0.1")

    # Not committed yet — a fresh query on the same session sees it (SQLAlchemy
    # autoflushes pending changes before a query), but rolling back removes it.
    assert db.query(AuditLogEntry).count() == 1
    db.rollback()
    assert db.query(AuditLogEntry).count() == 0


def test_record_with_no_user():
    db = _make_db()
    audit_log.record(db, user=None, action="login.failure", detail="unknown@example.com", ip_address="10.0.0.2")
    db.commit()

    entry = db.query(AuditLogEntry).first()
    assert entry.user_id is None
    assert entry.actor_email is None
    assert entry.detail == "unknown@example.com"


# ── routers/audit_log.py ──────────────────────────────────────────────────────

def _make_client_and_db():
    db = _make_db()
    from routers.audit_log import router as audit_log_router

    app = FastAPI()
    app.include_router(audit_log_router)
    app.dependency_overrides[get_db] = lambda: db
    admin = create_user(db, "admin@example.com", "hunter2hunter2", role=ROLE_ADMIN)
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[require_admin] = lambda: admin
    return TestClient(app), db


def test_list_entries_newest_first():
    client, db = _make_client_and_db()
    for i in range(3):
        audit_log.record(db, user=None, action=f"action.{i}", ip_address="10.0.0.1")
        db.commit()

    resp = client.get("/api/audit-log/")
    assert resp.status_code == 200, resp.text
    actions = [e["action"] for e in resp.json()]
    assert actions == ["action.2", "action.1", "action.0"]


def test_list_entries_respects_limit():
    client, db = _make_client_and_db()
    for i in range(5):
        audit_log.record(db, user=None, action=f"action.{i}", ip_address="10.0.0.1")
        db.commit()

    resp = client.get("/api/audit-log/?limit=2")
    assert len(resp.json()) == 2


# ── Instrumented mutation points ──────────────────────────────────────────────

def test_login_success_and_failure_are_audited():
    db = _make_db()
    from routers.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    create_user(db, "loginme@example.com", "correct-horse-battery-staple")

    resp = client.post("/api/auth/login", json={"email": "loginme@example.com", "password": "wrong"})
    assert resp.status_code == 401, resp.text
    resp = client.post("/api/auth/login", json={"email": "loginme@example.com", "password": "correct-horse-battery-staple"})
    assert resp.status_code == 200, resp.text

    actions = [e.action for e in db.query(AuditLogEntry).all()]
    assert "login.failure" in actions
    assert "login.success" in actions


def test_bootstrap_creates_admin_and_audits():
    db = _make_db()
    from routers.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    assert client.get("/api/auth/status").json() == {"setup_required": True}

    resp = client.post("/api/auth/bootstrap", json={"email": "first-admin@example.com", "password": "hunter2hunter2"})
    assert resp.status_code == 200, resp.text
    assert "token" in resp.json()

    assert client.get("/api/auth/status").json() == {"setup_required": False}

    # Self-disabling: a second bootstrap attempt is refused.
    resp = client.post("/api/auth/bootstrap", json={"email": "second@example.com", "password": "hunter2hunter2"})
    assert resp.status_code == 409, resp.text

    actions = [e.action for e in db.query(AuditLogEntry).all()]
    assert "user.create" in actions
    assert "login.success" in actions

    admin = db.query(User).filter(User.email == "first-admin@example.com").first()
    assert admin.role == ROLE_ADMIN


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
    print("\nRunning audit_log tests...\n")

    _run("record stages row without committing", test_record_stages_row_without_committing)
    _run("record with no user", test_record_with_no_user)
    _run("list entries newest first", test_list_entries_newest_first)
    _run("list entries respects limit", test_list_entries_respects_limit)
    _run("login success and failure are audited", test_login_success_and_failure_are_audited)
    _run("bootstrap creates admin and audits", test_bootstrap_creates_admin_and_audits)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
