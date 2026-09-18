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
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.auth as auth
from database import Base, get_db
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


def _request(client=..., headers=None):
    """Minimal ASGI scope wrapped in a real Request. Pass client=None to
    simulate a connection with no peer address."""
    scope = {"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]}
    if client is not ...:
        scope["client"] = client
    return Request(scope)


def test_client_ip_prefers_cf_connecting_ip():
    """Regression pin for the real T-08 vulnerability. CF-Connecting-IP is the
    only value a client cannot forge in this deployment, so it must win over
    both a spoofed X-Forwarded-For and the (also untrustworthy on Render)
    socket peer address."""
    request = _request(client=("10.0.0.5", 12345),
                       headers={"X-Forwarded-For": "203.0.113.9",
                                "CF-Connecting-IP": "198.51.100.42"})
    assert auth.client_ip(request) == "198.51.100.42"

    # Surrounding whitespace must not create a second identity for the same IP.
    padded = _request(client=("10.0.0.5", 12345),
                      headers={"CF-Connecting-IP": "  198.51.100.42  "})
    assert auth.client_ip(padded) == "198.51.100.42"


def test_client_ip_uses_cf_connecting_ip_without_a_peer():
    """CF-Connecting-IP must not need a socket peer to fall back on."""
    request = _request(client=None, headers={"CF-Connecting-IP": "198.51.100.42"})
    assert auth.client_ip(request) == "198.51.100.42"


def test_client_ip_ignores_x_forwarded_for():
    """With no CF-Connecting-IP present, client_ip() falls through to the
    socket peer address and must never read X-Forwarded-For — Cloudflare
    appends its own hops after whatever the client sent, so every position in
    that header (including the first) is attacker-controllable."""
    request = _request(client=("10.0.0.5", 12345), headers={"X-Forwarded-For": "203.0.113.9"})
    assert auth.client_ip(request) == "10.0.0.5"

    # Multi-hop / trailing-hop spoofs must be ignored just the same.
    chained = _request(client=("10.0.0.5", 12345),
                       headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.7"})
    assert auth.client_ip(chained) == "10.0.0.5"

    # True-Client-IP passed a live spoofing attempt through unblocked, so its
    # protection status here is unconfirmed and it is deliberately unused.
    true_client = _request(client=("10.0.0.5", 12345),
                           headers={"True-Client-IP": "203.0.113.9"})
    assert auth.client_ip(true_client) == "10.0.0.5"


def test_client_ip_falls_back_to_unknown_without_a_peer():
    assert auth.client_ip(_request(client=None)) == "unknown"
    assert auth.client_ip(_request()) == "unknown"  # no "client" key in scope at all


def test_login_failure_tracking_is_capped():
    """_login_failures must not grow without bound — an attacker cycling
    through distinct source IPs would otherwise leave permanent entries."""
    saved = dict(auth._login_failures)
    auth._login_failures.clear()
    try:
        overflow = 50
        ips = [f"198.18.{i // 256}.{i % 256}" for i in range(auth._MAX_TRACKED_IPS + overflow)]
        for ip in ips:
            auth.record_login_failure(ip)

        assert len(auth._login_failures) <= auth._MAX_TRACKED_IPS, len(auth._login_failures)
        # FIFO by insertion order: the oldest go first, the newest survive.
        assert ips[0] not in auth._login_failures
        assert ips[-1] in auth._login_failures
    finally:
        auth._login_failures.clear()
        auth._login_failures.update(saved)


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


# ── Router tests ──────────────────────────────────────────────────────────────

def test_login_case_insensitive_email():
    """Creating an account with mixed case, then logging in with different case should work."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.auth import router as auth_router

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    # Bootstrap with mixed case email
    resp = client.post("/api/auth/bootstrap", json={"email": "Admin@Example.com", "password": "correct-horse-battery-staple"})
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]

    # Login with different case should work
    resp = client.post("/api/auth/login", json={"email": "ADMIN@EXAMPLE.COM", "password": "correct-horse-battery-staple"})
    assert resp.status_code == 200, resp.text
    assert "token" in resp.json()

    # Login with yet another case should still work
    resp = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct-horse-battery-staple"})
    assert resp.status_code == 200, resp.text
    assert "token" in resp.json()


def test_login_legacy_mixed_case_email():
    """A user row with mixed-case email (created before T-10 normalization) should
    still be able to login with any case variation of that email."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.auth import router as auth_router
    from models.organization import Organization
    from services.user_service import hash_password

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    # Simulate a legacy user row with mixed-case email (created before normalization)
    org = Organization(name="Legacy.User@Example.com")
    db.add(org)
    db.flush()
    user = User(
        id="legacy-user-1",
        email="Legacy.User@Example.com",  # stored as-is, not normalized
        password_hash=hash_password("hunter2hunter2"),
        role="member",
        org_id=org.id
    )
    db.add(user)
    db.commit()

    # Login with lowercase email should work
    resp = client.post("/api/auth/login", json={"email": "legacy.user@example.com", "password": "hunter2hunter2"})
    assert resp.status_code == 200, resp.text
    assert "token" in resp.json()

    # Login with uppercase email should also work
    resp = client.post("/api/auth/login", json={"email": "LEGACY.USER@EXAMPLE.COM", "password": "hunter2hunter2"})
    assert resp.status_code == 200, resp.text
    assert "token" in resp.json()


def test_login_rejects_invalid_email_format():
    """Submitting an invalid email format should return 422."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.auth import router as auth_router

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    resp = client.post("/api/auth/login", json={"email": "not-an-email", "password": "hunter2hunter2"})
    assert resp.status_code == 422, resp.text


def test_bootstrap_rejects_invalid_email_format():
    """Bootstrap with invalid email format should return 422."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.auth import router as auth_router

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    resp = client.post("/api/auth/bootstrap", json={"email": "not-an-email", "password": "hunter2hunter2"})
    assert resp.status_code == 422, resp.text


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
    _run("client_ip prefers CF-Connecting-IP", test_client_ip_prefers_cf_connecting_ip)
    _run("client_ip uses CF-Connecting-IP without a peer", test_client_ip_uses_cf_connecting_ip_without_a_peer)
    _run("client_ip ignores X-Forwarded-For", test_client_ip_ignores_x_forwarded_for)
    _run("client_ip falls back to unknown without a peer", test_client_ip_falls_back_to_unknown_without_a_peer)
    _run("login failure tracking is capped", test_login_failure_tracking_is_capped)
    _run("login rate limit blocks after max attempts", test_login_rate_limit_blocks_after_max_attempts)
    _run("login rate limit resets on success", test_login_rate_limit_resets_on_success)
    _run("login rate limit window expiry", test_login_rate_limit_window_expiry)
    _run("login case insensitive email", test_login_case_insensitive_email)
    _run("login legacy mixed-case email", test_login_legacy_mixed_case_email)
    _run("login rejects invalid email format", test_login_rejects_invalid_email_format)
    _run("bootstrap rejects invalid email format", test_bootstrap_rejects_invalid_email_format)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
