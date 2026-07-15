"""Tests for services/auth.py — token lifecycle, get_current_user/require_admin,
and the per-IP login rate limiter.

Password hashing itself (bcrypt) is tested in test_user_service.py.

Run from the backend directory:
    ./venv/bin/python -m tests.test_auth
"""
import asyncio
import os
import sys
import time

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.auth as auth
from database import Base
from models.user import User
from services.secret_crypto import _fernet
from services.user_service import create_user


def _make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_token_round_trip():
    db = _make_db()
    user = create_user(db, "alice@example.com", "correct-horse-battery-staple")
    token = auth.create_token(user)
    assert auth.verify_token(token) == user.id
    assert auth.verify_token("garbage-token") is None
    assert auth.verify_token("") is None


def test_expired_token_is_rejected():
    """A token issued long ago (beyond session TTL) must fail verification."""
    ttl_seconds = 12 * 3600  # default session_ttl_hours
    old = _fernet().encrypt_at_time(b'{"uid": "some-id"}', int(time.time()) - ttl_seconds - 60)
    assert auth.verify_token(old.decode()) is None


def test_pre_migration_token_format_is_rejected():
    """Tokens from before per-user accounts encrypted a fixed plaintext, not
    JSON — decrypts fine but must fail json.loads and be treated as invalid,
    not crash."""
    old_style = _fernet().encrypt(b"authenticated")
    assert auth.verify_token(old_style.decode()) is None


def test_get_current_user_rejects_missing_and_bad_headers():
    db = _make_db()
    for header in (None, "", "Basic abc", "Bearer garbage", "Bearer "):
        try:
            asyncio.run(auth.get_current_user(authorization=header, db=db))
            raise AssertionError(f"expected 401 for header {header!r}")
        except HTTPException as exc:
            assert exc.status_code == 401, exc.status_code


def test_get_current_user_accepts_valid_token():
    db = _make_db()
    user = create_user(db, "bob@example.com", "hunter2hunter2")
    token = auth.create_token(user)
    resolved = asyncio.run(auth.get_current_user(authorization=f"Bearer {token}", db=db))
    assert resolved.id == user.id
    resolved2 = asyncio.run(auth.get_current_user(authorization=f"bearer {token}", db=db))  # case-insensitive scheme
    assert resolved2.id == user.id


def test_get_current_user_rejects_inactive_user():
    db = _make_db()
    user = create_user(db, "carol@example.com", "hunter2hunter2")
    token = auth.create_token(user)
    user.is_active = False
    db.commit()
    try:
        asyncio.run(auth.get_current_user(authorization=f"Bearer {token}", db=db))
        raise AssertionError("expected 401 for a deactivated user")
    except HTTPException as exc:
        assert exc.status_code == 401, exc.status_code


def test_get_current_user_rejects_deleted_user():
    db = _make_db()
    user = create_user(db, "dave@example.com", "hunter2hunter2")
    token = auth.create_token(user)
    db.delete(user)
    db.commit()
    try:
        asyncio.run(auth.get_current_user(authorization=f"Bearer {token}", db=db))
        raise AssertionError("expected 401 for an unknown user id")
    except HTTPException as exc:
        assert exc.status_code == 401, exc.status_code


def test_require_admin_accepts_admin_rejects_member():
    admin = User(id="admin-1", email="a@example.com", password_hash="x", role="admin")
    member = User(id="member-1", email="m@example.com", password_hash="x", role="member")

    asyncio.run(auth.require_admin(current_user=admin))  # must not raise
    try:
        asyncio.run(auth.require_admin(current_user=member))
        raise AssertionError("expected 403 for a member")
    except HTTPException as exc:
        assert exc.status_code == 403, exc.status_code


def test_login_rate_limit_blocks_after_max_attempts():
    ip = "203.0.113.1"
    auth.clear_login_failures(ip)
    try:
        for _ in range(auth._LOGIN_MAX_ATTEMPTS):
            auth.check_login_rate_limit(ip)  # must not raise yet
            auth.record_login_failure(ip)

        try:
            auth.check_login_rate_limit(ip)
            raise AssertionError("expected 429 after max failed attempts")
        except HTTPException as exc:
            assert exc.status_code == 429, exc.status_code
            assert "Retry-After" in exc.headers
    finally:
        auth.clear_login_failures(ip)


def test_login_rate_limit_resets_on_success():
    ip = "203.0.113.2"
    auth.clear_login_failures(ip)
    try:
        for _ in range(auth._LOGIN_MAX_ATTEMPTS):
            auth.record_login_failure(ip)
        auth.clear_login_failures(ip)  # simulates a successful login
        auth.check_login_rate_limit(ip)  # must not raise — budget reset
    finally:
        auth.clear_login_failures(ip)


def test_login_rate_limit_window_expiry():
    ip = "203.0.113.3"
    auth.clear_login_failures(ip)
    try:
        old = time.monotonic() - auth._LOGIN_ATTEMPT_WINDOW_SECONDS - 1
        auth._login_failures[ip] = [old] * auth._LOGIN_MAX_ATTEMPTS
        auth.check_login_rate_limit(ip)  # stale attempts fall outside window — must not raise
    finally:
        auth.clear_login_failures(ip)


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
    print("\nRunning auth tests...\n")

    _run("token round trip", test_token_round_trip)
    _run("expired token is rejected", test_expired_token_is_rejected)
    _run("pre-migration token format is rejected", test_pre_migration_token_format_is_rejected)
    _run("get_current_user rejects bad headers", test_get_current_user_rejects_missing_and_bad_headers)
    _run("get_current_user accepts valid token", test_get_current_user_accepts_valid_token)
    _run("get_current_user rejects inactive user", test_get_current_user_rejects_inactive_user)
    _run("get_current_user rejects deleted user", test_get_current_user_rejects_deleted_user)
    _run("require_admin accepts admin, rejects member", test_require_admin_accepts_admin_rejects_member)
    _run("login rate limit blocks after max attempts", test_login_rate_limit_blocks_after_max_attempts)
    _run("login rate limit resets on success", test_login_rate_limit_resets_on_success)
    _run("login rate limit window expiry", test_login_rate_limit_window_expiry)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
