"""
Activity log viewer — admin-only (see main.py: Depends(require_admin) at
include_router time).

GET /api/audit-log?limit=&before= – paginated, newest first
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database import get_db
from models.audit_log import AuditLogEntry

router = APIRouter(prefix="/api/audit-log", tags=["audit-log"])


@router.get("/")
async def list_entries(
    limit: int = Query(default=50, ge=1, le=200),
    before: str | None = Query(default=None, description="ISO timestamp cursor — entries older than this"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    q = db.query(AuditLogEntry)
    if before:
        try:
            cursor = datetime.fromisoformat(before)
            q = q.filter(AuditLogEntry.created_at < cursor)
        except ValueError:
            pass
    entries = q.order_by(AuditLogEntry.created_at.desc()).limit(limit).all()
    return [
        {
            "id": e.id,
            "actor_email": e.actor_email,
            "action": e.action,
            "resource_type": e.resource_type,
            "resource_id": e.resource_id,
            "detail": e.detail,
            "ip_address": e.ip_address,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]
