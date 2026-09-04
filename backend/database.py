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
    from models import document, report, research_session, debate, digest_settings, model_settings, reminder, user, organization, audit_log, custom_persona, model_catalog, usage_event  # noqa: F401
    Base.metadata.create_all(bind=engine)
    encrypt_legacy_secrets()
    normalize_legacy_report_status()
    add_citation_confidence_columns()
    add_debate_consensus_column()
    add_dimension_confidence_column()
    add_source_tier_column()
    add_ownership_columns()
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


def add_dimension_confidence_column():
    """Idempotent migration: add the dimension_confidence column to risk_analyses.

    Stores the per-dimension grounding grades that risk_analyzer._fix_weak_dimensions
    already computed but previously discarded — it graded every dimension, used the
    scores only to pick which ones to re-research, and then threw them away. The
    column keeps them so the UI can show "score 8/10, but grounded at 4/10".
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        try:
            conn.execute(
                text("ALTER TABLE risk_analyses ADD COLUMN dimension_confidence TEXT")
            )
        except Exception:
            pass  # column already exists, or table doesn't exist yet


def add_source_tier_column():
    """Idempotent migration: add the source_tier column to search_results.

    Records who published each source (regulator, peer-reviewed venue, news
    organisation, advocacy group, vendor) so the citation list can show it.
    Rows predating this stay NULL and render as unclassified — classifying them
    on read would cost one LLM call per source per page view.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE search_results ADD COLUMN source_tier VARCHAR"))
        except Exception:
            pass  # column already exists, or table doesn't exist yet


# Content tables that carry ownership (user_id + org_id). Their child tables
# (document_chunks, report_sections, search_results, debate_arguments) are
# deliberately NOT listed: they are reachable only through their scoped parent,
# so scoping the parent scopes them.
OWNERSHIP_TABLES = (
    "documents", "reports", "research_sessions", "risk_analyses", "debates", "reminders",
)


def _table_columns(conn, table: str) -> set[str]:
    """Column names of `table`, or an empty set if the table doesn't exist."""
    from sqlalchemy import text

    try:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    except Exception:
        return set()
    return {r[1] for r in rows}


def add_ownership_columns():
    """Idempotent migration: give every content row an owner.

    Three steps, each safe to re-run:

      1. Add users.org_id and user_id/org_id (+ their indexes) to each of
         OWNERSHIP_TABLES, skipping any column that already exists.
      2. Create one organization per user (name = the user's email) and point
         users.org_id at it, skipping users that already have one. One
         organization per user is the current tenancy model — see
         models/organization.py.
      3. Backfill: every content row still lacking an owner is assigned to the
         OLDEST admin account and that admin's organization. Pre-migration rows
         have no recorded author, and the oldest admin is the account that
         (in this single-tenant deployment) created them.

    A database with no users at all — a fresh deploy, or a test DB — is a
    no-op: the columns are added and steps 2/3 are skipped. Same if there are
    users but no admin to assign to (logged, never raised): init_db must not
    fail because of an unusual user table.
    """
    import uuid as _uuid
    from datetime import datetime as _datetime

    from sqlalchemy import text

    added_columns: list[str] = []
    orgs_created = 0
    rows_backfilled: dict[str, int] = {}
    note = ""

    with engine.begin() as conn:
        # 1. columns + indexes
        for table in ("users",) + OWNERSHIP_TABLES:
            existing = _table_columns(conn, table)
            if not existing:
                continue  # table not created yet
            wanted = ("org_id",) if table == "users" else ("user_id", "org_id")
            for col in wanted:
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} VARCHAR"))
                    added_columns.append(f"{table}.{col}")
                # SQLAlchemy's create_all only indexes tables it creates, so an
                # existing table needs its index created here. Name matches
                # SQLAlchemy's ix_<table>_<column> convention so create_all on a
                # fresh database produces the same schema.
                conn.execute(
                    text(f"CREATE INDEX IF NOT EXISTS ix_{table}_{col} ON {table} ({col})")
                )

        # 2. one organization per user
        if not _table_columns(conn, "users"):
            return
        users = conn.execute(
            text("SELECT id, email, role, org_id FROM users ORDER BY created_at")
        ).fetchall()
        if not users:
            print("Ownership migration: no users yet — columns only.")
            return

        for user_id, email, _role, org_id in users:
            if org_id:
                continue
            new_org_id = str(_uuid.uuid4())
            conn.execute(
                text("INSERT INTO organizations (id, name, created_at) VALUES (:id, :name, :ts)"),
                {"id": new_org_id, "name": email, "ts": _datetime.utcnow()},
            )
            conn.execute(
                text("UPDATE users SET org_id = :org WHERE id = :id"),
                {"org": new_org_id, "id": user_id},
            )
            orgs_created += 1

        # 3. backfill content rows to the oldest admin
        owner = conn.execute(
            text("SELECT id, org_id FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1")
        ).first()
        if owner is None:
            note = " (no admin account — content rows left unowned)"
        else:
            for table in OWNERSHIP_TABLES:
                if not _table_columns(conn, table):
                    continue
                result = conn.execute(
                    text(
                        f"UPDATE {table} SET user_id = :uid, org_id = :oid "
                        f"WHERE user_id IS NULL"
                    ),
                    {"uid": owner[0], "oid": owner[1]},
                )
                if result.rowcount:
                    rows_backfilled[table] = result.rowcount

    backfilled = ", ".join(f"{t}={n}" for t, n in rows_backfilled.items()) or "none"
    print(
        f"Ownership migration: columns added={len(added_columns)} "
        f"({', '.join(added_columns) or 'none'}), organizations created={orgs_created}, "
        f"rows backfilled: {backfilled}{note}"
    )


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
