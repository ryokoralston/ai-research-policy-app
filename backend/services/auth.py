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

from fastapi import Header, HTTPException, status
from cryptography.fernet import InvalidToken

from config import get_settings
from services.secret_crypto import _fernet

_TOKEN_PLAINTEXT = b"authenticated"


def auth_enabled() -> bool:
    return bool(get_settings().app_password)


def check_password(password: str) -> bool:
    """Constant-time comparison against the configured password."""
    expected = get_settings().app_password
    if not expected:
        return False
    return hmac.compare_digest(password, expected)


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
