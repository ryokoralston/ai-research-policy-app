"""Tests for routers/users.py — admin user management.

Covers list/create/update, the last-active-admin protection (can't
deactivate or demote your way down to zero admins), and that create_user
audit-logs.

Run from the backend directory:
    ./venv/bin/python -m tests.test_users_router
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
from services.auth import get_current_user, require_admin
from services.user_service import ROLE_ADMIN, create_user


def _make_client_and_db(current_user=None):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    from routers.users import router as users_router

    app = FastAPI()
    app.include_router(users_router)
    app.dependency_overrides[get_db] = lambda: db

    admin = current_user or create_user(db, "admin@example.com", "hunter2hunter2", role=ROLE_ADMIN)
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[require_admin] = lambda: admin
    return TestClient(app), db, admin


def test_create_and_list_users():
    client, db, _admin = _make_client_and_db()

    resp = client.post("/api/users/", json={"email": "new@example.com", "password": "hunter2hunter2"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == "new@example.com"
    assert body["role"] == "member"
    assert "password" not in body and "password_hash" not in body

    listing = client.get("/api/users/").json()
    emails = {u["email"] for u in listing}
    assert "new@example.com" in emails
    assert "admin@example.com" in emails  # the seeded admin


def test_create_user_rejects_duplicate_email():
    client, db, _admin = _make_client_and_db()
    client.post("/api/users/", json={"email": "dup@example.com", "password": "hunter2hunter2"})
    resp = client.post("/api/users/", json={"email": "dup@example.com", "password": "hunter2hunter2"})
    assert resp.status_code == 409, resp.text


def test_create_user_rejects_short_password():
    client, db, _admin = _make_client_and_db()
    resp = client.post("/api/users/", json={"email": "short@example.com", "password": "short"})
    assert resp.status_code == 400, resp.text


def test_update_role_and_deactivate():
    client, db, _admin = _make_client_and_db()
    created = client.post("/api/users/", json={"email": "target@example.com", "password": "hunter2hunter2"}).json()

    resp = client.patch(f"/api/users/{created['id']}", json={"role": "admin"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"

    resp = client.patch(f"/api/users/{created['id']}", json={"is_active": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False


def test_cannot_deactivate_last_active_admin():
    client, db, admin = _make_client_and_db()
    resp = client.patch(f"/api/users/{admin.id}", json={"is_active": False})
    assert resp.status_code == 400, resp.text


def test_cannot_demote_last_active_admin():
    client, db, admin = _make_client_and_db()
    resp = client.patch(f"/api/users/{admin.id}", json={"role": "member"})
    assert resp.status_code == 400, resp.text


def test_can_deactivate_admin_when_another_admin_remains():
    client, db, admin = _make_client_and_db()
    second_admin = create_user(db, "second-admin@example.com", "hunter2hunter2", role=ROLE_ADMIN)

    resp = client.patch(f"/api/users/{admin.id}", json={"is_active": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False


def test_reset_password():
    client, db, _admin = _make_client_and_db()
    created = client.post("/api/users/", json={"email": "reset@example.com", "password": "hunter2hunter2"}).json()

    resp = client.patch(f"/api/users/{created['id']}", json={"new_password": "brand-new-password-1"})
    assert resp.status_code == 200, resp.text

    from services.user_service import verify_password
    user = db.get(User, created["id"])
    assert verify_password("brand-new-password-1", user.password_hash)
    assert not verify_password("hunter2hunter2", user.password_hash)


def test_create_user_writes_audit_entry():
    client, db, admin = _make_client_and_db()
    client.post("/api/users/", json={"email": "audited@example.com", "password": "hunter2hunter2"})

    entries = db.query(AuditLogEntry).filter(AuditLogEntry.action == "user.create").all()
    assert len(entries) == 1, entries
    assert entries[0].actor_email == admin.email
    assert "audited@example.com" in entries[0].detail


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
    print("\nRunning users router tests...\n")

    _run("create and list users", test_create_and_list_users)
    _run("create user rejects duplicate email", test_create_user_rejects_duplicate_email)
    _run("create user rejects short password", test_create_user_rejects_short_password)
    _run("update role and deactivate", test_update_role_and_deactivate)
    _run("cannot deactivate last active admin", test_cannot_deactivate_last_active_admin)
    _run("cannot demote last active admin", test_cannot_demote_last_active_admin)
    _run("can deactivate admin when another remains", test_can_deactivate_admin_when_another_admin_remains)
    _run("reset password", test_reset_password)
    _run("create user writes audit entry", test_create_user_writes_audit_entry)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
