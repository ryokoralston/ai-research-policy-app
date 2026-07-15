"""
Lightweight single-password auth for the whole API.

A user logs in with the shared APP_PASSWORD and receives an opaque bearer token
(a Fernet token with a built-in timestamp, signed by the same key used for
secret encryption). Protected routes validate the token via ``require_auth``.

Auth is enforced only when ``APP_PASSWORD`` is configured. When it is empty,
``require_auth`` is a no-op so local development keeps working unauthenticated
(main.py logs a warning at startup in that case).
"""
from __future__ import annotations

import hmac
import time

from fastapi import Header, HTTPException, Request, status
from cryptography.fernet import InvalidToken

from config import get_settings
from services.secret_crypto import _fernet

_TOKEN_PLAINTEXT = b"authenticated"

# Per-IP failed-login throttle. In-memory only — fine for this app's single-
# instance Render deployment; a restart or a second instance would reset the
# budget, which just falls back to no throttling rather than failing closed.
_LOGIN_ATTEMPT_WINDOW_SECONDS = 300  # 5 minutes
_LOGIN_MAX_ATTEMPTS = 5
_login_failures: dict[str, list[float]] = {}


def auth_enabled() -> bool:
    return bool(get_settings().app_password)


def check_password(password: str) -> bool:
    """Constant-time comparison against the configured password."""
    expected = get_settings().app_password
    if not expected:
        return False
    return hmac.compare_digest(password, expected)


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


def create_token() -> str:
    """Issue a signed, time-stamped bearer token."""
    return _fernet().encrypt(_TOKEN_PLAINTEXT).decode()


def verify_token(token: str) -> bool:
    """Return True if the token is valid and not older than the session TTL."""
    ttl = get_settings().session_ttl_hours * 3600
    try:
        data = _fernet().decrypt(token.encode(), ttl=ttl)
    except InvalidToken:
        return False
    return data == _TOKEN_PLAINTEXT


async def require_auth(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: enforce a valid bearer token on protected routes."""
    if not auth_enabled():
        return  # auth disabled — allow through

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ", 1)[1].strip()
    if not verify_token(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
