"""Tests for services/auth.py — password check, token lifecycle, require_auth.

Environment (APP_PASSWORD, SECRET_ENCRYPTION_KEY) is set before importing
config, since get_settings() is lru_cached.

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
os.environ["APP_PASSWORD"] = "correct-horse-battery-staple"

from cryptography.fernet import Fernet

os.environ["SECRET_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from fastapi import HTTPException

import services.auth as auth
from services.secret_crypto import _fernet


def test_check_password():
    assert auth.check_password("correct-horse-battery-staple")
    assert not auth.check_password("wrong-password")
    assert not auth.check_password("")


def test_token_round_trip():
    token = auth.create_token()
    assert auth.verify_token(token)
    assert not auth.verify_token("garbage-token")
    assert not auth.verify_token("")


def test_expired_token_is_rejected():
    """A token issued long ago (beyond session TTL) must fail verification."""
    ttl_seconds = 12 * 3600  # default session_ttl_hours
    old = _fernet().encrypt_at_time(b"authenticated", int(time.time()) - ttl_seconds - 60)
    assert not auth.verify_token(old.decode())


def test_require_auth_rejects_missing_and_bad_headers():
    for header in (None, "", "Basic abc", "Bearer garbage", "Bearer "):
        try:
            asyncio.run(auth.require_auth(header))
            raise AssertionError(f"expected 401 for header {header!r}")
        except HTTPException as exc:
            assert exc.status_code == 401, exc.status_code


def test_require_auth_accepts_valid_token():
    token = auth.create_token()
    asyncio.run(auth.require_auth(f"Bearer {token}"))  # must not raise
    asyncio.run(auth.require_auth(f"bearer {token}"))  # scheme is case-insensitive


def test_require_auth_noop_when_disabled():
    original = auth.auth_enabled
    auth.auth_enabled = lambda: False
    try:
        asyncio.run(auth.require_auth(None))  # must not raise
    finally:
        auth.auth_enabled = original


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

    _run("check_password constant-time compare", test_check_password)
    _run("token round trip", test_token_round_trip)
    _run("expired token is rejected", test_expired_token_is_rejected)
    _run("require_auth rejects bad headers", test_require_auth_rejects_missing_and_bad_headers)
    _run("require_auth accepts valid token", test_require_auth_accepts_valid_token)
    _run("require_auth no-op when disabled", test_require_auth_noop_when_disabled)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
