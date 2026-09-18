"""Tests for scripts/restore_report_from_sections.py.

The invariant under test: reconstructing `Report.content` from its
`ReportSection` rows (canonical order = order_index) must reproduce the
EXACT pre-revision assembly format report_generator.py used to write —
"# {title}\\n\\n" + "\\n\\n---\\n\\n".join("## {title}\\n\\n{content}" ...) —
and the script must NEVER write unless that reconstruction is strictly more
complete (more words) than what's currently stored, whether or not --apply
is passed.

Isolation: a fresh in-memory SQLAlchemy DB per test (same pattern as
tests/test_reconcile.py and tests/test_report_section_chaining.py) — nothing
here touches the real dev database.

Run from the backend directory:
    ./venv/bin/python -m tests.test_restore_report_script
"""
import os
import sys
import uuid
from datetime import datetime

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models import Report, ReportSection
from scripts.restore_report_from_sections import restore_report

_SEED_UPDATED_AT = datetime(2020, 1, 1)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _mem_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_report(db, title="Test Report", content="short", status="completed") -> str:
    report_id = str(uuid.uuid4())
    db.add(Report(
        id=report_id, title=title, report_type="policy_memo", status=status,
        content=content, word_count=len(content.split()) if content else 0,
        updated_at=_SEED_UPDATED_AT,
    ))
    db.commit()
    return report_id


def _add_section(db, report_id, order_index, key, title, content) -> None:
    db.add(ReportSection(
        id=str(uuid.uuid4()), report_id=report_id, section_key=key,
        title=title, content=content, order_index=order_index,
    ))
    db.commit()


# ── (a) sections more complete than content → dry run writes nothing ─────────

def test_dry_run_does_not_write_when_sections_are_more_complete():
    db = _mem_db()
    try:
        report_id = _make_report(db, title="Test Report", content="a truncated stub")
        # Inserted out of order_index order — proves the relationship's
        # order_by, not insertion order, drives assembly.
        _add_section(db, report_id, 1, "second", "Second", "gamma")
        _add_section(db, report_id, 0, "first", "First", "alpha beta")

        result = restore_report(db, report_id, apply=False)

        # Reconstructed = "# Test Report\n\n## First\n\nalpha beta\n\n---\n\n## Second\n\ngamma"
        # split() counts markdown markers ("#", "##", "---") as words too — 11 total.
        assert result["status"] == "dry_run", result
        assert result["current_words"] == 3, result
        assert result["restored_words"] == 11, result

        db.expire_all()
        reread = db.get(Report, report_id)
        assert reread.content == "a truncated stub", reread.content
        assert reread.word_count == 3, reread.word_count
        assert reread.updated_at == _SEED_UPDATED_AT, reread.updated_at
    finally:
        db.close()


# ── (b) sections more complete than content → apply writes exact format ──────

def test_apply_writes_exact_reconstructed_format_and_updates_metadata():
    db = _mem_db()
    try:
        report_id = _make_report(db, title="Test Report", content="a truncated stub")
        _add_section(db, report_id, 1, "second", "Second", "gamma")
        _add_section(db, report_id, 0, "first", "First", "alpha beta")

        result = restore_report(db, report_id, apply=True)

        assert result["status"] == "restored", result
        assert result["current_words"] == 3, result
        assert result["restored_words"] == 11, result

        expected = "# Test Report\n\n## First\n\nalpha beta\n\n---\n\n## Second\n\ngamma"

        db.expire_all()
        reread = db.get(Report, report_id)
        assert reread.content == expected, reread.content
        assert reread.word_count == 11, reread.word_count
        assert reread.updated_at != _SEED_UPDATED_AT, "updated_at was not touched"
    finally:
        db.close()


# ── (c) content already at least as complete → no write, dry run ─────────────

def test_no_write_when_current_content_already_longer_dry_run():
    db = _mem_db()
    try:
        long_content = "one two three four five six seven eight nine ten"
        report_id = _make_report(db, title="Test Report", content=long_content)
        _add_section(db, report_id, 0, "first", "First", "alpha beta")

        result = restore_report(db, report_id, apply=False)

        assert result["status"] == "not_improved", result

        db.expire_all()
        reread = db.get(Report, report_id)
        assert reread.content == long_content, reread.content
        assert reread.updated_at == _SEED_UPDATED_AT, reread.updated_at
    finally:
        db.close()


# ── (c') same, but apply=True is passed — the guard must still hold ──────────

def test_no_write_when_current_content_already_longer_even_with_apply():
    """Safety invariant: passing apply=True must not bypass the
    strictly-more-complete guard. This proves the guard isn't just a
    dry-run default that apply skips."""
    db = _mem_db()
    try:
        long_content = "one two three four five six seven eight nine ten"
        report_id = _make_report(db, title="Test Report", content=long_content)
        _add_section(db, report_id, 0, "first", "First", "alpha beta")

        result = restore_report(db, report_id, apply=True)

        assert result["status"] == "not_improved", result

        db.expire_all()
        reread = db.get(Report, report_id)
        assert reread.content == long_content, reread.content
        assert reread.word_count == len(long_content.split()), reread.word_count
        assert reread.updated_at == _SEED_UPDATED_AT, (
            "updated_at was touched despite the safety guard"
        )
    finally:
        db.close()


# ── (c'') equal word counts is also "not improved", not a tie-break write ────

def test_equal_word_counts_does_not_write():
    db = _mem_db()
    try:
        report_id = _make_report(db, title="Test Report", content="placeholder")
        _add_section(db, report_id, 0, "first", "First", "alpha beta")
        # Reconstructed = "# Test Report\n\n## First\n\nalpha beta" → split() counts
        # "#" and "##" as words too: #, Test, Report, ##, First, alpha, beta = 7.
        # Set current content to exactly 7 words too, to test the tie boundary.
        seven_words = "aaa bbb ccc ddd eee fff ggg"
        report = db.get(Report, report_id)
        report.content = seven_words
        db.commit()

        result = restore_report(db, report_id, apply=True)

        assert result["status"] == "not_improved", result
        assert result["current_words"] == result["restored_words"] == 7, result

        db.expire_all()
        reread = db.get(Report, report_id)
        assert reread.content == seven_words, reread.content
    finally:
        db.close()


# ── (d) zero sections → clean exit, no write, no exception ───────────────────

def test_zero_sections_exits_cleanly_without_writing():
    db = _mem_db()
    try:
        report_id = _make_report(db, title="Test Report", content="whatever content")

        result = restore_report(db, report_id, apply=True)

        assert result["status"] == "no_sections", result

        db.expire_all()
        reread = db.get(Report, report_id)
        assert reread.content == "whatever content", reread.content
        assert reread.updated_at == _SEED_UPDATED_AT, reread.updated_at
    finally:
        db.close()


# ── (e) unknown report id → error status, no exception/traceback ─────────────

def test_unknown_report_id_returns_error_without_raising():
    db = _mem_db()
    try:
        result = restore_report(db, "does-not-exist", apply=True)
        assert result["status"] == "not_found", result
    finally:
        db.close()


# ── (f) duplicate order_index → refuse rather than guess an order ────────────

def test_duplicate_order_index_refuses_to_write():
    db = _mem_db()
    try:
        report_id = _make_report(db, title="Test Report", content="x")
        _add_section(db, report_id, 0, "first", "First", "alpha beta gamma")
        _add_section(db, report_id, 0, "second", "Second", "delta epsilon zeta")

        result = restore_report(db, report_id, apply=True)

        assert result["status"] == "duplicate_order_index", result

        db.expire_all()
        reread = db.get(Report, report_id)
        assert reread.content == "x", reread.content
    finally:
        db.close()


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
    print("\nRunning restore_report_from_sections tests...\n")

    _run("dry run does not write when sections are more complete", test_dry_run_does_not_write_when_sections_are_more_complete)
    _run("apply writes exact reconstructed format and updates metadata", test_apply_writes_exact_reconstructed_format_and_updates_metadata)
    _run("no write when current content already longer (dry run)", test_no_write_when_current_content_already_longer_dry_run)
    _run("no write when current content already longer (even with apply)", test_no_write_when_current_content_already_longer_even_with_apply)
    _run("equal word counts does not write", test_equal_word_counts_does_not_write)
    _run("zero sections exits cleanly without writing", test_zero_sections_exits_cleanly_without_writing)
    _run("unknown report id returns error without raising", test_unknown_report_id_returns_error_without_raising)
    _run("duplicate order_index refuses to write", test_duplicate_order_index_refuses_to_write)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
