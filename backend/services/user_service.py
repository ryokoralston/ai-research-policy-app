"""
User account helpers: password hashing (bcrypt) and lookups.

Roles are a flat two-value scheme — 'admin' (full access, including user
management and the activity log) and 'member' (everything else). No
self-registration: admins create accounts directly (see routers/users.py)
or the first admin is created via the one-time bootstrap flow
(routers/auth.py) when the users table is empty.
"""
from __future__ import annotations

import bcrypt
from sqlalchemy.orm import Session

from models.user import User

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/legacy hash — never valid, but don't crash the login request.
        return False


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def create_user(db: Session, email: str, password: str, role: str = ROLE_MEMBER) -> User:
    user = User(email=email, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def count_active_admins(db: Session, exclude_user_id: str | None = None) -> int:
    """Number of active admins, optionally excluding one user id — used to
    guard against demoting/deactivating the last remaining admin."""
    q = db.query(User).filter(User.role == ROLE_ADMIN, User.is_active.is_(True))
    if exclude_user_id:
        q = q.filter(User.id != exclude_user_id)
    return q.count()
