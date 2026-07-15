"""
Auth router — bootstrap, login, and the current user's own profile/password.

POST /api/auth/bootstrap   – create the first admin account (only while no
                              users exist yet — see GET /status)
POST /api/auth/login       – exchange email + password for a bearer token
GET  /api/auth/status      – whether the app still needs its first admin
GET  /api/auth/me          – the current user's own profile
POST /api/auth/me/password – change the current user's own password

/status, /bootstrap, and /login are intentionally NOT protected by
get_current_user (you cannot present a token before you have one; bootstrap
is the one-time exception to needing an account at all).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models.user import User
from services import audit_log, user_service
from services.auth import (
    check_login_rate_limit,
    clear_login_failures,
    client_ip,
    create_token,
    get_current_user,
    record_login_failure,
)
from services.user_service import ROLE_ADMIN

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class BootstrapIn(BaseModel):
    email: str
    password: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


def _setup_required(db: Session) -> bool:
    return db.query(User).first() is None


@router.get("/status")
async def auth_status(db: Session = Depends(get_db)) -> dict:
    return {"setup_required": _setup_required(db)}


@router.post("/bootstrap")
async def bootstrap(body: BootstrapIn, request: Request, db: Session = Depends(get_db)) -> dict:
    """Create the first admin account. Self-disabling: 409s the moment any
    user exists, so this never needs an env var or a Render dashboard step —
    the user just opens the app once after deploy."""
    if not _setup_required(db):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Setup already completed")
    if len(body.password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")

    user = user_service.create_user(db, body.email, body.password, role=ROLE_ADMIN)
    user.last_login_at = datetime.utcnow()
    db.commit()

    ip = client_ip(request)
    audit_log.record(db, user=user, action="user.create", resource_type="user", resource_id=user.id,
                      detail="initial admin bootstrap", ip_address=ip)
    audit_log.record(db, user=user, action="login.success", ip_address=ip)
    db.commit()

    return {
        "token": create_token(user),
        "expires_in": get_settings().session_ttl_hours * 3600,
    }


@router.post("/login")
async def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> dict:
    ip = client_ip(request)
    check_login_rate_limit(ip)  # raises 429 if this IP is currently throttled

    user = user_service.get_user_by_email(db, body.email)
    if not user or not user.is_active or not user_service.verify_password(body.password, user.password_hash):
        record_login_failure(ip)
        audit_log.record(db, user=None, action="login.failure", detail=body.email, ip_address=ip)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    clear_login_failures(ip)
    user.last_login_at = datetime.utcnow()
    audit_log.record(db, user=user, action="login.success", ip_address=ip)
    db.commit()

    return {
        "token": create_token(user),
        "expires_in": get_settings().session_ttl_hours * 3600,
    }


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)) -> dict:
    return {"id": current_user.id, "email": current_user.email, "role": current_user.role}


@router.post("/me/password")
async def change_own_password(
    body: PasswordChangeIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not user_service.verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")

    current_user.password_hash = user_service.hash_password(body.new_password)
    audit_log.record(db, user=current_user, action="user.password_change", resource_type="user",
                      resource_id=current_user.id, ip_address=client_ip(request))
    db.commit()
    return {"ok": True}
