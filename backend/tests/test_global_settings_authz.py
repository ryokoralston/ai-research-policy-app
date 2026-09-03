"""Tests that the two global singleton settings rows are admin-only to write.

`model_settings` (API key + model choice) and `digest_settings` (recipient
address + SMTP password) are each a single row shared by the whole deployment,
so a member writing to them changes the app for everyone. Both PUTs used to
accept any signed-in user.

These deliberately override only `get_current_user`, leaving the real
`require_admin` in place, so the 403 comes from the dependency under test.

Run from the backend directory:
    ./venv/bin/python -m tests.test_global_settings_authz
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

from database import Base, get_db, get_or_init_model_settings

# Both singleton models are imported lazily inside database.get_or_init_*, so
# without these they would not be registered on Base.metadata when create_all
# runs below and the tables would be missing.
from models.digest_settings import DigestSettings  # noqa: F401
from models.model_settings import ModelSettings  # noqa: F401
from services.auth import get_current_user
from services.user_service import ROLE_ADMIN, ROLE_MEMBER, create_user


def _make_client_and_db(role):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    from routers.settings import router as settings_router
    from routers.digest import router as digest_router

    app = FastAPI()
    app.include_router(settings_router)
    app.include_router(digest_router)
    app.dependency_overrides[get_db] = lambda: db

    user = create_user(db, f"{role}@example.com", "hunter2hunter2", role=role)
    app.dependency_overrides[get_current_user] = lambda: user

    return TestClient(app), db


def test_member_cannot_overwrite_the_shared_api_key():
    client, db = _make_client_and_db(ROLE_MEMBER)
    resp = client.put("/api/settings/models", json={"anthropic_api_key": "sk-ant-attacker-key"})
    assert resp.status_code == 403, resp.text

    ms = get_or_init_model_settings(db)
    assert ms.anthropic_api_key != "sk-ant-attacker-key"


def test_admin_can_save_model_settings():
    client, _ = _make_client_and_db(ROLE_ADMIN)
    resp = client.put("/api/settings/models", json={"claude_model": "claude-opus-4-6"})
    assert resp.status_code == 200, resp.text


def test_member_cannot_redirect_the_digest():
    client, _ = _make_client_and_db(ROLE_MEMBER)
    resp = client.put(
        "/api/digest/settings",
        json={"email_to": "attacker@example.com", "email_from": "attacker@example.com"},
    )
    assert resp.status_code == 403, resp.text


def test_admin_can_save_digest_settings():
    client, _ = _make_client_and_db(ROLE_ADMIN)
    resp = client.put("/api/digest/settings", json={"email_to": "owner@example.com"})
    assert resp.status_code == 200, resp.text


def test_member_cannot_send_digest_now():
    client, _ = _make_client_and_db(ROLE_MEMBER)
    resp = client.post("/api/digest/send-now")
    assert resp.status_code == 403, resp.text


def test_admin_can_send_digest_now_when_configured():
    """Admin can trigger send-now; returns 400 only if settings missing, not 403."""
    client, _ = _make_client_and_db(ROLE_ADMIN)
    # send-now will fail with 400 if email settings are not configured, but
    # not with 403 due to auth. We just confirm it's not 403.
    resp = client.post("/api/digest/send-now")
    assert resp.status_code != 403, f"Admin got 403: {resp.text}"


def test_reads_stay_open_to_members():
    """Only the writes were tightened — members still see (masked) settings."""
    client, _ = _make_client_and_db(ROLE_MEMBER)
    assert client.get("/api/settings/models").status_code == 200
    assert client.get("/api/digest/settings").status_code == 200


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
    print("\nRunning global settings authz tests...\n")

    _run("member cannot overwrite the shared API key", test_member_cannot_overwrite_the_shared_api_key)
    _run("admin can save model settings", test_admin_can_save_model_settings)
    _run("member cannot redirect the digest", test_member_cannot_redirect_the_digest)
    _run("admin can save digest settings", test_admin_can_save_digest_settings)
    _run("member cannot send digest now", test_member_cannot_send_digest_now)
    _run("admin can send digest now when configured", test_admin_can_send_digest_now_when_configured)
    _run("reads stay open to members", test_reads_stay_open_to_members)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
