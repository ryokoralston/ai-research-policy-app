"""
Per-user auth for the whole API.

A user logs in with their email + password and receives an opaque bearer
token (a Fernet token with a built-in timestamp, signed by the same key
used for secret encryption, encrypting a small JSON payload identifying the
user). Protected routes resolve the acting user via ``get_current_user``;
admin-only routes additionally require ``require_admin``.

Auth is always required — there is no shared-password/no-auth escape hatch
(that model was retired in favor of real accounts). A brand-new deployment
with an empty users table is handled by the one-time bootstrap flow in
routers/auth.py, not by disabling auth here.
"""
from __future__ import annotations

import json
import time

from fastapi import Depends, Header, HTTPException, Request, status
from cryptography.fernet import InvalidToken
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models.user import User
from services.secret_crypto import _fernet

# Per-IP failed-login throttle. In-memory only — fine for this app's single-
# instance Render deployment; a restart or a second instance would reset the
# budget, which just falls back to no throttling rather than failing closed.
_LOGIN_ATTEMPT_WINDOW_SECONDS = 300  # 5 minutes
_LOGIN_MAX_ATTEMPTS = 5
_login_failures: dict[str, list[float]] = {}


def client_ip(request: Request) -> str:
    """Best-effort caller IP: first hop of X-Forwarded-For (set by Render's
    proxy) if present, else the direct connection's address."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_login_rate_limit(ip: str) -> None:
    """Raise 429 if `ip` has exceeded the failed-login budget in the current
    window. Called before checking the password, so a locked-out caller can't
    keep guessing."""
    now = time.monotonic()
    attempts = [t for t in _login_failures.get(ip, []) if now - t < _LOGIN_ATTEMPT_WINDOW_SECONDS]
    if attempts:
        _login_failures[ip] = attempts
    else:
        _login_failures.pop(ip, None)

    if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
        retry_after = int(_LOGIN_ATTEMPT_WINDOW_SECONDS - (now - attempts[0])) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def record_login_failure(ip: str) -> None:
    _login_failures.setdefault(ip, []).append(time.monotonic())


def clear_login_failures(ip: str) -> None:
    _login_failures.pop(ip, None)


def create_token(user: User) -> str:
    """Issue a signed, time-stamped bearer token identifying `user`."""
    payload = json.dumps({"uid": user.id}).encode("utf-8")
    return _fernet().encrypt(payload).decode()


def verify_token(token: str) -> str | None:
    """Return the encoded user id if `token` is valid and not older than the
    session TTL, else None. Also rejects tokens from before this app's
    per-user-accounts migration (those encrypted a fixed plaintext, not JSON)
    — decrypts fine but fails json.loads, which is treated the same as any
    other invalid token."""
    ttl = get_settings().session_ttl_hours * 3600
    try:
        data = _fernet().decrypt(token.encode(), ttl=ttl)
    except InvalidToken:
        return None
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    uid = payload.get("uid")
    return uid if isinstance(uid, str) else None


async def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: resolve and return the acting user, or 401."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized

    token = authorization.split(" ", 1)[1].strip()
    uid = verify_token(token)
    if not uid:
        raise unauthorized

    user = db.get(User, uid)
    if user is None or not user.is_active:
        raise unauthorized
    return user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency: like get_current_user, but 403s non-admins."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user
