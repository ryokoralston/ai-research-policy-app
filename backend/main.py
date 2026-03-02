import logging
import os
from contextlib import asynccontextmanager

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from database import SessionLocal, init_db, get_or_init_digest_settings
from routers import research, documents, reports, analysis, debate
from routers.digest import router as digest_router, record_sent, set_scheduler
from routers.settings import router as settings_router
from services.digest_service import send_digest

logger = logging.getLogger(__name__)


async def _run_digest() -> None:
    """Scheduled job wrapper — skips silently if not configured."""
    with SessionLocal() as db:
        ds = get_or_init_digest_settings(db)
        if not ds.email_to:
            logger.info("Digest skipped: email_to not set.")
            return
        topics = [t.strip() for t in ds.topics.split(",") if t.strip()]
        email_to = ds.email_to
        email_from = ds.email_from
        smtp_password = ds.smtp_password
    try:
        result = await send_digest(
            email_to=email_to,
            email_from=email_from,
            smtp_password=smtp_password,
            topics=topics,
        )
        record_sent(result["sent_at"])
        logger.info("Scheduled digest sent: %s", result)
    except Exception:
        logger.exception("Scheduled digest failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings = get_settings()
    os.makedirs(settings.uploads_dir, exist_ok=True)
    os.makedirs(settings.chroma_persist_dir, exist_ok=True)
    init_db()

    with SessionLocal() as db:
        ds = get_or_init_digest_settings(db)
        digest_hour = ds.send_hour
        digest_tz = ds.timezone

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_digest,
        CronTrigger(
            hour=digest_hour,
            minute=0,
            timezone=pytz.timezone(digest_tz),
        ),
        id="daily_digest",
        replace_existing=True,
    )
    scheduler.start()
    set_scheduler(scheduler)
    logger.info(
        "APScheduler started — digest scheduled at %02d:00 %s",
        digest_hour,
        digest_tz,
    )

    yield

    # Shutdown
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="AI Policy Research App",
    description="AI-powered policy research assistant for congressional briefings and risk analysis.",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(research.router)
app.include_router(documents.router)
app.include_router(reports.router)
app.include_router(analysis.router)
app.include_router(debate.router)
app.include_router(digest_router)
app.include_router(settings_router)


@app.get("/health")
def health():
    return {"status": "ok"}
