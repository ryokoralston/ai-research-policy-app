"""
Digest router — settings, manual trigger, and status endpoints.

GET  /api/digest/settings   – get digest settings from DB
PUT  /api/digest/settings   – save digest settings to DB and reschedule
POST /api/digest/send-now   – send the digest immediately (for testing)
GET  /api/digest/status     – last sent time, next scheduled time, config summary
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, get_or_init_digest_settings
from services.digest_service import send_digest
from utils.masking import MASK, mask_secret

router = APIRouter(prefix="/api/digest", tags=["digest"])

logger = logging.getLogger(__name__)


# In-memory state (resets on restart — intentional lightweight design)
_last_sent_at: str | None = None
_scheduler: AsyncIOScheduler | None = None


def set_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Called from main.py to share the scheduler instance."""
    global _scheduler
    _scheduler = scheduler


def record_sent(sent_at: str) -> None:
    global _last_sent_at
    _last_sent_at = sent_at


def reschedule_digest(hour: int, tz: str) -> None:
    """Reschedule the daily_digest APScheduler job with new hour/timezone."""
    if _scheduler is not None:
        try:
            _scheduler.reschedule_job(
                "daily_digest",
                trigger=CronTrigger(
                    hour=hour,
                    minute=0,
                    timezone=pytz.timezone(tz),
                ),
            )
        except Exception:
            # Job may not exist yet on first run — keep going, but leave a trace.
            logger.warning("Could not reschedule daily_digest job", exc_info=True)


class DigestSettingsIn(BaseModel):
    email_to: str | None = None
    email_from: str | None = None
    smtp_password: str | None = None
    topics: str | None = None
    timezone: str | None = None
    send_hour: int | None = None


@router.get("/settings")
async def get_settings_endpoint(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return current digest settings from DB."""
    ds = get_or_init_digest_settings(db)
    return {
        "email_to": ds.email_to,
        "email_from": ds.email_from,
        "smtp_password": mask_secret(ds.smtp_password),
        "topics": ds.topics,
        "timezone": ds.timezone,
        "send_hour": ds.send_hour,
        "updated_at": ds.updated_at.isoformat() if ds.updated_at else None,
    }


@router.put("/settings")
async def save_settings_endpoint(
    body: DigestSettingsIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Save digest settings to DB and reschedule the job if hour/timezone changed."""
    ds = get_or_init_digest_settings(db)

    if body.email_to is not None:
        ds.email_to = body.email_to
    if body.email_from is not None:
        ds.email_from = body.email_from
    # Only update the password when a real new value is sent — ignore empty
    # strings and the masked sentinel echoed back by the frontend.
    if body.smtp_password and body.smtp_password != MASK:
        ds.smtp_password = body.smtp_password
    if body.topics is not None:
        ds.topics = body.topics
    if body.timezone is not None:
        ds.timezone = body.timezone
    if body.send_hour is not None:
        ds.send_hour = body.send_hour

    ds.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(ds)

    reschedule_digest(ds.send_hour, ds.timezone)

    return {
        "email_to": ds.email_to,
        "email_from": ds.email_from,
        "smtp_password": mask_secret(ds.smtp_password),
        "topics": ds.topics,
        "timezone": ds.timezone,
        "send_hour": ds.send_hour,
        "updated_at": ds.updated_at.isoformat() if ds.updated_at else None,
    }


@router.post("/send-now")
async def send_now(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Immediately send the digest (test endpoint)."""
    ds = get_or_init_digest_settings(db)
    topics = [t.strip() for t in ds.topics.split(",") if t.strip()]
    try:
        result = await send_digest(
            email_to=ds.email_to,
            email_from=ds.email_from,
            smtp_password=ds.smtp_password,
            topics=topics,
        )
        record_sent(result["sent_at"])
        return {"success": True, **result}
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to send digest: {exc}") from exc


@router.get("/status")
async def get_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Return scheduler status and configuration."""
    ds = get_or_init_digest_settings(db)

    next_run: str | None = None
    if _scheduler is not None:
        jobs = _scheduler.get_jobs()
        digest_jobs = [j for j in jobs if j.id == "daily_digest"]
        if digest_jobs and digest_jobs[0].next_run_time:
            next_run = digest_jobs[0].next_run_time.isoformat()

    tz = pytz.timezone(ds.timezone)
    now_local = datetime.now(tz)
    topics = [t.strip() for t in ds.topics.split(",") if t.strip()]

    return {
        "configured": bool(ds.email_to),
        "recipient": ds.email_to or "(not set)",
        "sender": ds.email_from or "(not set)",
        "topics": topics,
        "schedule": f"Daily at {ds.send_hour:02d}:00 {ds.timezone}",
        "current_time_local": now_local.strftime("%Y-%m-%d %H:%M %Z"),
        "last_sent_at": _last_sent_at,
        "next_run_at": next_run,
    }
