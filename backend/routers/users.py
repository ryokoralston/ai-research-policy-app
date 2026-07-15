"""
User management — admin-only (see main.py: this router carries
Depends(require_admin) at include_router time).

GET   /api/users       – list all users
POST  /api/users       – create a user
PATCH /api/users/{id}  – update role / is_active / reset password

No DELETE — users are soft-deactivated (is_active=False) to keep audit-log
history readable and avoid orphaning foreign keys.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models.user import User
from services import audit_log, user_service
from services.auth import client_ip, require_admin
from services.user_service import ROLE_ADMIN, ROLE_MEMBER

router = APIRouter(prefix="/api/users", tags=["users"])

_ROLES = {ROLE_ADMIN, ROLE_MEMBER}


class UserCreateIn(BaseModel):
    email: str
    password: str
    role: str = ROLE_MEMBER


class UserUpdateIn(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    new_password: str | None = None


def _serialize(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    }


@router.get("/")
async def list_users(db: Session = Depends(get_db)) -> list[dict]:
    users = db.query(User).order_by(User.created_at).all()
    return [_serialize(u) for u in users]


@router.post("/")
async def create_user(
    body: UserCreateIn,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if body.role not in _ROLES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"role must be one of {sorted(_ROLES)}")
    if len(body.password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")
    if user_service.get_user_by_email(db, body.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A user with that email already exists")

    user = user_service.create_user(db, body.email, body.password, role=body.role)
    audit_log.record(db, user=current_user, action="user.create", resource_type="user", resource_id=user.id,
                      detail=f"created {user.email} ({user.role})", ip_address=client_ip(request))
    db.commit()
    return _serialize(user)


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdateIn,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    changes: list[str] = []

    would_deactivate = body.is_active is False and user.is_active
    would_demote = body.role is not None and body.role != ROLE_ADMIN and user.role == ROLE_ADMIN
    if (would_deactivate or would_demote) and user_service.count_active_admins(db, exclude_user_id=user.id) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the last active admin",
        )

    if body.role is not None and body.role != user.role:
        if body.role not in _ROLES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"role must be one of {sorted(_ROLES)}")
        changes.append(f"role: {user.role} -> {body.role}")
        user.role = body.role

    if body.is_active is not None and body.is_active != user.is_active:
        changes.append("activated" if body.is_active else "deactivated")
        user.is_active = body.is_active

    if body.new_password is not None:
        if len(body.new_password) < 8:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")
        user.password_hash = user_service.hash_password(body.new_password)
        changes.append("password reset")

    if changes:
        audit_log.record(db, user=current_user, action="user.update", resource_type="user", resource_id=user.id,
                          detail=f"{user.email}: {', '.join(changes)}", ip_address=client_ip(request))
    db.commit()
    db.refresh(user)
    return _serialize(user)
