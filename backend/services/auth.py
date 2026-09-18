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
# Ceiling on distinct IPs tracked at once. Entries are only pruned when that
# same IP is looked up again, so without a cap an attacker cycling through
# source addresses could grow this dict indefinitely. Oldest-inserted entries
# are evicted first (FIFO) — losing an old entry just means that IP starts its
# window over, which is the same as a process restart.
_MAX_TRACKED_IPS = 10_000
_login_failures: dict[str, list[float]] = {}


def client_ip(request: Request) -> str:
    """The caller's IP, taken from ``CF-Connecting-IP`` when Cloudflare is in
    front, otherwise from the raw TCP peer address.

    This is the second attempt at this fix. Read the whole docstring before
    changing it — the platform behavior here is not what the deployment docs
    for a plain uvicorn app would lead you to expect.

    Render fronts *all* ``*.onrender.com`` traffic with Cloudflare as its own
    platform-level DDoS protection (nothing in this project configured it;
    responses from the live backend carry ``server: cloudflare`` and a
    ``cf-ray`` header). That gives us exactly one header the client cannot
    choose, and two that look usable but are not:

    * ``CF-Connecting-IP`` — trusted, and what this function returns. On
      2026-09-18 a request that tried to supply its own ``CF-Connecting-IP:
      5.6.7.8`` was rejected by Cloudflare's edge with a ``403`` before it
      ever reached Render or this app (no ``x-render-origin-server`` header on
      the response, so uvicorn never saw the request). A client therefore
      cannot get a forged value into this header in this topology. Note this
      is an empirically observed edge behavior, not a documented contract:
      Cloudflare's HTTP-headers reference describes what the header *means*
      but does not promise it overwrites a client-supplied value. It is a
      single address (v4 or v6), never a comma-separated chain, so there is
      nothing to split.
    * ``X-Forwarded-For`` — never read. On a legitimate request its first hop
      genuinely *is* the real client IP (real captured example through
      Render+Cloudflare: ``81.97.145.24, 172.71.195.88, 10.226.90.65`` = true
      client, Cloudflare edge, Render internal proxy). That is precisely the
      trap: Cloudflare's own docs state it *appends* its hop to an
      ``X-Forwarded-For`` that was already present, and nothing strips a
      client-supplied value first. So an attacker prepends whatever they like
      and owns the first position end to end, which is what made the original
      ``.split(",")[0]`` logic exploitable.
    * ``True-Client-IP`` — never read. A self-supplied ``True-Client-IP:
      5.6.7.8`` was *not* blocked and reached the app normally, so whatever
      protection it has on this zone/plan is unconfirmed. Cloudflare documents
      it as equivalent to ``CF-Connecting-IP``, but the observed asymmetry
      says otherwise here, so it stays unused.

    ``request.client.host`` alone — the previous fix, commit ``7e40ba0``, which
    was deployed to production — is ALSO insufficient and is only a fallback
    now. After that deploy went live the original spoof was re-run against
    production and still succeeded: ``X-Forwarded-For: 203.0.113.9`` was still
    recorded under that exact fabricated value. This was not a deploy-timing
    race — Render's dashboard confirmed that commit was the live deploy and
    the test timestamps were correlated against it. Whatever sits between
    Cloudflare and the app container appears to derive the value Starlette
    exposes as ``request.client.host`` from the same attacker-influenced XFF
    chain rather than from the genuine raw socket peer. That mechanism is
    inferred, not proven, but the failure itself is measured. It is
    Render-specific, so do not reach for uvicorn's ``--proxy-headers``: it is
    fed by the same untrustworthy client-supplied header either way.

    The fallback exists for a topology with no Cloudflare in front at all
    (local development, or some future host), where ``CF-Connecting-IP`` is
    simply absent. That is also this function's one trust boundary: it trusts
    the header whenever it is present, which is safe only because Cloudflare
    is unconditionally in front of ``*.onrender.com``. If this app ever
    becomes reachable at an origin that bypasses Cloudflare, a client could
    then set ``CF-Connecting-IP`` freely and both the failed-login throttle
    and the audit log would be spoofable again.
    """
    cf_connecting_ip = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf_connecting_ip:
        return cf_connecting_ip
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
    while len(_login_failures) > _MAX_TRACKED_IPS:
        _login_failures.pop(next(iter(_login_failures)))


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
