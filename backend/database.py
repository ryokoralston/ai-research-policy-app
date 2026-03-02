import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import get_settings


class Base(DeclarativeBase):
    pass


def get_engine():
    settings = get_settings()
    db_url = settings.database_url
    # Ensure data directory exists for SQLite
    if db_url.startswith("sqlite"):
        db_path = db_url.replace("sqlite:///", "")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return create_engine(db_url, connect_args={"check_same_thread": False})


engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    # Import all models so Base knows about them
    from models import document, report, research_session, debate, digest_settings, model_settings  # noqa: F401
    Base.metadata.create_all(bind=engine)
    print("Database initialized.")


def get_or_init_model_settings(db):
    """Return the single ModelSettings row, creating it from .env defaults if absent."""
    from models.model_settings import ModelSettings

    ms = db.get(ModelSettings, 1)
    if ms is None:
        settings = get_settings()
        ms = ModelSettings(
            id=1,
            main_model=settings.claude_model,
            fast_model=settings.claude_fast_model,
            anthropic_api_key=settings.anthropic_api_key,
            openai_api_key="",
        )
        db.add(ms)
        db.commit()
        db.refresh(ms)
    return ms


def get_or_init_digest_settings(db):
    """Return the single DigestSettings row, creating it from .env defaults if absent."""
    from models.digest_settings import DigestSettings

    ds = db.get(DigestSettings, 1)
    if ds is None:
        settings = get_settings()
        ds = DigestSettings(
            id=1,
            email_to=settings.digest_email_to,
            email_from=settings.digest_email_from,
            smtp_password=settings.digest_smtp_password,
            topics=settings.digest_topics,
            timezone=settings.digest_timezone,
            send_hour=settings.digest_hour,
        )
        db.add(ds)
        db.commit()
        db.refresh(ds)
    return ds
