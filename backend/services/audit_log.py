"""
Actor-attribution log for security/administratively-sensitive operations:
login success/failure, user management, and the "major" mutations called
out during the 2026-07 per-user-accounts pass (settings changes, document
deletion). Not a full audit trail of every mutation in the app — see
models/audit_log.py's AuditLogEntry for the shape.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from models.audit_log import AuditLogEntry
from models.user import User


def record(
    db: Session,
    *,
    user: User | None,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Stage an audit row on `db` without committing — it lands atomically
    with whatever db.commit() the caller already does for the action being
    recorded, so a failed mutation never leaves behind an orphan audit entry
    for something that didn't actually happen."""
    db.add(AuditLogEntry(
        user_id=user.id if user else None,
        actor_email=user.email if user else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
        ip_address=ip_address,
    ))
