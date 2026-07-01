"""Tests for the canonical report status value ('completed').

Covers:
  1. Sectioned generation path sets status='completed'
  2. Single-pass (word-limit) generation path sets status='completed'
  3. normalize_legacy_report_status() migrates 'complete' → 'completed'
     idempotently and leaves other statuses untouched

No Claude API / network calls — stream_text is monkeypatched with a fake
async generator. Run from the backend directory:
    ./venv/bin/python -m tests.test_report_status

Uses a plain assert-based runner because pytest is not installed in the venv.
"""
import asyncio
import os
import sys
import uuid

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# Point the app at an in-memory SQLite DB so importing `database` never touches disk.
os.environ.setdefault("DATABASE_URL", "sqlite://")

# ── Imports ───────────────────────────────────────────────────────────────────
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import database
from database import Base
from models import Report, ResearchSession  # registers tables with Base
from schemas import ReportGenerateRequest
import services.report_generator as report_generator


# ── Fakes ─────────────────────────────────────────────────────────────────────

async def _fake_stream_text(prompt, system="", model=None, max_tokens=8192, temperature=1.0):
    """Stand-in for anthropic_client.stream_text: yields a fixed short text."""
    for token in ["Lorem ", "ipsum ", "section ", "content."]:
        yield token


def _make_test_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def _seed_report_and_session(db):
    """Insert a ResearchSession (source material) and a draft Report row."""
    session = ResearchSession(
        id=str(uuid.uuid4()),
        query="AI policy test query",
        status="complete",
        summary="A short research synthesis used as source material.",
    )
    db.add(session)
    report_id = str(uuid.uuid4())
    report = Report(
        id=report_id,
        title="Test Report",
        report_type="policy_memo",
        status="draft",
        session_id=session.id,
    )
    db.add(report)
    db.commit()
    return report_id, session.id


async def _run_generation(db, request, report_id):
    events = []
    async for event in report_generator.generate_report_stream(report_id, request, db):
        events.append(event)
    return events


# ── Generation path tests ─────────────────────────────────────────────────────

def test_sectioned_generation_sets_completed():
    """Section-by-section path saves the report with status='completed'."""
    original = report_generator.stream_text
    report_generator.stream_text = _fake_stream_text
    try:
        db = _make_test_session()
        report_id, session_id = _seed_report_and_session(db)
        request = ReportGenerateRequest(
            report_type="policy_memo",
            title="Test Report",
            session_id=session_id,
        )
        events = asyncio.run(_run_generation(db, request, report_id))
        report = db.query(Report).filter(Report.id == report_id).first()
        assert report.status == "completed", f"expected 'completed', got {report.status!r}"
        assert report.content, "report content should be saved"
        assert any('"event_type": "complete"' in e for e in events), "complete event expected"
        db.close()
    finally:
        report_generator.stream_text = original


def test_single_pass_generation_sets_completed():
    """Word-limit (single-pass) path saves the report with status='completed'."""
    original = report_generator.stream_text
    report_generator.stream_text = _fake_stream_text
    try:
        db = _make_test_session()
        report_id, session_id = _seed_report_and_session(db)
        request = ReportGenerateRequest(
            report_type="policy_memo",
            title="Test Report",
            session_id=session_id,
            custom_instructions="200 words or less",  # triggers _generate_single_pass
        )
        events = asyncio.run(_run_generation(db, request, report_id))
        report = db.query(Report).filter(Report.id == report_id).first()
        assert report.status == "completed", f"expected 'completed', got {report.status!r}"
        assert any('"section": "full_report"' in e for e in events), "single-pass path expected"
        db.close()
    finally:
        report_generator.stream_text = original


# ── Migration tests ───────────────────────────────────────────────────────────

def _insert_report_raw(status):
    rid = str(uuid.uuid4())
    with database.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reports (id, title, report_type, status, created_at, updated_at) "
                "VALUES (:id, 'T', 'policy_memo', :status, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": rid, "status": status},
        )
    return rid


def _status_of(rid):
    with database.engine.begin() as conn:
        return conn.execute(
            text("SELECT status FROM reports WHERE id = :id"), {"id": rid}
        ).scalar()


def test_migration_rewrites_legacy_complete():
    """'complete' rows become 'completed'; other statuses are untouched."""
    Base.metadata.create_all(bind=database.engine)
    legacy = _insert_report_raw("complete")
    draft = _insert_report_raw("draft")
    done = _insert_report_raw("completed")
    in_review = _insert_report_raw("in_review")

    database.normalize_legacy_report_status()

    assert _status_of(legacy) == "completed", _status_of(legacy)
    assert _status_of(draft) == "draft", _status_of(draft)
    assert _status_of(done) == "completed", _status_of(done)
    assert _status_of(in_review) == "in_review", _status_of(in_review)


def test_migration_is_idempotent():
    """Running the migration twice changes nothing further."""
    Base.metadata.create_all(bind=database.engine)
    legacy = _insert_report_raw("complete")
    database.normalize_legacy_report_status()
    database.normalize_legacy_report_status()
    assert _status_of(legacy) == "completed", _status_of(legacy)


# ── Test runner ───────────────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")


if __name__ == "__main__":
    print("\nRunning report status tests...\n")

    _run("sectioned generation sets status='completed'", test_sectioned_generation_sets_completed)
    _run("single-pass generation sets status='completed'", test_single_pass_generation_sets_completed)
    _run("migration rewrites 'complete' -> 'completed'", test_migration_rewrites_legacy_complete)
    _run("migration is idempotent", test_migration_is_idempotent)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
