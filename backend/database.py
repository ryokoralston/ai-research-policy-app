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
    from models import document, report, research_session, debate, digest_settings, model_settings, reminder, user, audit_log  # noqa: F401
    Base.metadata.create_all(bind=engine)
    encrypt_legacy_secrets()
    normalize_legacy_report_status()
    add_citation_confidence_columns()
    add_debate_consensus_column()
    print("Database initialized.")


def normalize_legacy_report_status():
    """Idempotent migration: rewrite legacy report status 'complete' → 'completed'.

    The report generator historically wrote 'complete' on finish, while the
    PATCH endpoint and the frontend use the workflow vocabulary
    draft|in_review|pre_approval|completed. 'completed' is the canonical value;
    this normalizes rows created before the generator was fixed.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        try:
            conn.execute(
                text("UPDATE reports SET status = 'completed' WHERE status = 'complete'")
            )
        except Exception:
            pass  # table may not exist yet


def add_citation_confidence_columns():
    """Idempotent migration: add the citation_confidence column to risk_analyses.

    Backs the citation/grounding-verification feature — stores the JSON result
    of services.citation_verifier.verify_grounding() (confidence_score,
    unsupported_claims, notes) alongside the existing risk_scores column.
    Report.metadata_json already exists and needs no schema change; the
    verification result for reports is merged into that existing JSON blob
    instead (see report_generator.py's _merge_metadata_json helper).
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        try:
            conn.execute(
                text("ALTER TABLE risk_analyses ADD COLUMN citation_confidence TEXT")
            )
        except Exception:
            pass  # column already exists, or table doesn't exist yet


def add_debate_consensus_column():
    """Idempotent migration: add the consensus column to debates.

    Backs the Consensus Meter feature — stores the JSON result of
    services.consensus_meter.extract_consensus() (3-5 debated claims, each
    with every persona's agree/disagree/mixed stance) alongside the existing
    synthesis column.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        try:
            conn.execute(
                text("ALTER TABLE debates ADD COLUMN consensus TEXT")
            )
        except Exception:
            pass  # column already exists, or table doesn't exist yet


def encrypt_legacy_secrets():
    """Idempotent migration: encrypt any secret columns still stored as plaintext.

    Reads the RAW stored value (bypassing the EncryptedString decrypt) so it can
    distinguish legacy plaintext from already-encrypted values. encrypt_secret()
    is a no-op on already-encrypted input, so running this repeatedly is safe.
    """
    from sqlalchemy import text
    from services.secret_crypto import encrypt_secret

    # (table, column) pairs are hard-coded constants — safe to interpolate.
    secret_cols = [
        ("model_settings", "anthropic_api_key"),
        ("model_settings", "openai_api_key"),
        ("digest_settings", "smtp_password"),
    ]
    with engine.begin() as conn:
        for table, col in secret_cols:
            try:
                rows = conn.execute(text(f"SELECT id, {col} FROM {table}")).fetchall()
            except Exception:
                continue  # table may not exist yet
            for row_id, val in rows:
                if not val:
                    continue
                enc = encrypt_secret(val)
                if enc != val:  # value was plaintext — write it back encrypted
                    conn.execute(
                        text(f"UPDATE {table} SET {col} = :v WHERE id = :id"),
                        {"v": enc, "id": row_id},
                    )


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
