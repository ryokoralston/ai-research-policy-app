"""Unit tests for reminder tool functions.

No Claude API / network calls — all tests exercise the Python functions directly.
Run from the backend directory:
    ./venv/bin/python -m tests.test_reminder_tools

Uses a plain assert-based runner because pytest is not installed in the venv.
"""
import asyncio
import os
import sys

# ── Path setup ────────────────────────────────────────────────────────────────
# Ensure the backend package root is on sys.path so imports resolve.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# Point the app at an in-memory SQLite DB so importing `database` never touches disk.
os.environ.setdefault("DATABASE_URL", "sqlite://")

# ── Imports ───────────────────────────────────────────────────────────────────
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import Base and the Reminder model after env var is set so the real engine
# never fires.  We create our own in-memory engine for tests.
import database as _db_module  # noqa: F401 — side-effect: sets Base.metadata

from database import Base
from models.reminder import Reminder  # registers table with Base
from services.reminder_tools import (
    _get_current_datetime,
    _add_duration_to_datetime,
    _set_reminder,
    execute_reminder_tool,
)


# ── In-memory DB session factory for set_reminder tests ───────────────────────

def _make_test_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


# ── Test runner helpers ───────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run(name: str, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")


# ── get_current_datetime tests ────────────────────────────────────────────────

def test_get_current_datetime_default_format_parseable():
    """Default format returns a string that strptime can parse back."""
    result = _get_current_datetime({})
    # Format: "Current datetime: YYYY-MM-DD HH:MM:SS, weekday: <Name>"
    assert result.startswith("Current datetime: "), repr(result)
    dt_part = result.split("Current datetime: ")[1].split(", weekday:")[0]
    parsed = datetime.strptime(dt_part, "%Y-%m-%d %H:%M:%S")
    assert isinstance(parsed, datetime)


def test_get_current_datetime_custom_format_hhmm():
    """Custom format %H:%M returns HH:MM followed by the weekday name."""
    result = _get_current_datetime({"date_format": "%H:%M"})
    assert result.startswith("Current datetime: "), repr(result)
    dt_part = result.split("Current datetime: ")[1].split(", weekday:")[0]
    # Should match HH:MM
    parsed = datetime.strptime(dt_part, "%H:%M")
    assert isinstance(parsed, datetime)
    assert "weekday:" in result


def test_get_current_datetime_empty_format_returns_error():
    """Empty date_format should return a clear error string mentioning 'date_format'."""
    result = _get_current_datetime({"date_format": ""})
    assert "Error" in result, repr(result)
    assert "date_format" in result, repr(result)


def test_get_current_datetime_non_string_format_returns_error():
    """Non-string date_format (e.g. a number) should return a clear error string."""
    result = _get_current_datetime({"date_format": 123})
    assert "Error" in result and "date_format" in result, repr(result)


def test_get_current_datetime_no_input():
    """Calling with None input uses the default format."""
    result = _get_current_datetime(None)
    assert result.startswith("Current datetime: "), repr(result)
    dt_part = result.split("Current datetime: ")[1].split(", weekday:")[0]
    datetime.strptime(dt_part, "%Y-%m-%d %H:%M:%S")  # should not raise


# ── add_duration_to_datetime tests ────────────────────────────────────────────

def test_add_duration_one_month_over_boundary():
    """2026-01-31 + 1 month should clamp to 2026-02-28 (dateutil relativedelta)."""
    result = _add_duration_to_datetime({
        "datetime": "2026-01-31 00:00:00",
        "duration": 1,
        "unit": "months",
    })
    assert "2026-02-28" in result, repr(result)


def test_add_duration_two_weeks():
    """2026-06-12 + 2 weeks = 2026-06-26."""
    result = _add_duration_to_datetime({
        "datetime": "2026-06-12 00:00:00",
        "duration": 2,
        "unit": "weeks",
    })
    assert "2026-06-26" in result, repr(result)


def test_add_duration_weekday_correctness():
    """2026-06-12 is a Friday — verify weekday name in result."""
    result = _add_duration_to_datetime({
        "datetime": "2026-06-12 00:00:00",
        "duration": 0,
        "unit": "days",
    })
    assert "Friday" in result, repr(result)


def test_add_duration_missing_datetime():
    """Missing 'datetime' returns an Error string mentioning the field."""
    result = _add_duration_to_datetime({"duration": 1, "unit": "days"})
    assert "Error" in result and "datetime" in result.lower(), repr(result)


def test_add_duration_bad_unit():
    """Unsupported unit 'fortnight' returns an Error string mentioning the value."""
    result = _add_duration_to_datetime({
        "datetime": "2026-06-12 00:00:00",
        "duration": 1,
        "unit": "fortnight",
    })
    assert "Error" in result and "fortnight" in result, repr(result)


def test_add_duration_non_numeric_duration():
    """Non-numeric duration returns an Error string mentioning 'duration'."""
    result = _add_duration_to_datetime({
        "datetime": "2026-06-12 00:00:00",
        "duration": "soon",
        "unit": "days",
    })
    assert "Error" in result and "duration" in result.lower(), repr(result)


def test_add_duration_unparseable_datetime():
    """Garbage datetime string returns an Error string."""
    result = _add_duration_to_datetime({
        "datetime": "not-a-date",
        "duration": 1,
        "unit": "days",
    })
    assert "Error" in result, repr(result)


# ── set_reminder tests ────────────────────────────────────────────────────────

def test_set_reminder_happy_path_persists_row():
    """Happy path: stores a reminder row with the correct due_at."""
    db = _make_test_session()
    result = asyncio.run(_set_reminder(
        {"content": "Submit the policy memo", "timestamp": "2026-06-26T09:00:00"},
        db,
    ))
    assert "Reminder set" in result, repr(result)
    row = db.query(Reminder).first()
    assert row is not None, "Expected a Reminder row in DB"
    assert row.content == "Submit the policy memo"
    assert row.due_at == datetime(2026, 6, 26, 9, 0, 0)
    db.close()


def test_set_reminder_empty_content_returns_error_persists_nothing():
    """Empty content returns an Error string and writes no row."""
    db = _make_test_session()
    result = asyncio.run(_set_reminder(
        {"content": "", "timestamp": "2026-06-26T09:00:00"},
        db,
    ))
    assert "Error" in result and "content" in result.lower(), repr(result)
    assert db.query(Reminder).count() == 0, "No row should be persisted on error"
    db.close()


def test_set_reminder_bad_timestamp_returns_error_persists_nothing():
    """Unparseable timestamp returns an Error string and writes no row."""
    db = _make_test_session()
    result = asyncio.run(_set_reminder(
        {"content": "Some reminder", "timestamp": "not-a-timestamp"},
        db,
    ))
    assert "Error" in result, repr(result)
    assert db.query(Reminder).count() == 0, "No row should be persisted on error"
    db.close()


# ── execute_reminder_tool dispatch tests ──────────────────────────────────────

def test_execute_reminder_tool_unknown_returns_none():
    """Dispatching an unknown tool name returns None."""
    db = _make_test_session()
    result = asyncio.run(execute_reminder_tool("unknown_tool", {}, db))
    assert result is None, repr(result)
    db.close()


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\nRunning reminder tool tests...\n")

    _run("get_current_datetime: default format parseable", test_get_current_datetime_default_format_parseable)
    _run("get_current_datetime: custom format %H:%M", test_get_current_datetime_custom_format_hhmm)
    _run("get_current_datetime: empty format -> error", test_get_current_datetime_empty_format_returns_error)
    _run("get_current_datetime: non-string format -> error", test_get_current_datetime_non_string_format_returns_error)
    _run("get_current_datetime: no input uses default", test_get_current_datetime_no_input)

    _run("add_duration: +1 month over boundary (Jan 31 -> Feb 28)", test_add_duration_one_month_over_boundary)
    _run("add_duration: +2 weeks", test_add_duration_two_weeks)
    _run("add_duration: weekday correctness (2026-06-12 = Friday)", test_add_duration_weekday_correctness)
    _run("add_duration: missing datetime -> error", test_add_duration_missing_datetime)
    _run("add_duration: bad unit 'fortnight' -> error", test_add_duration_bad_unit)
    _run("add_duration: non-numeric duration -> error", test_add_duration_non_numeric_duration)
    _run("add_duration: unparseable datetime -> error", test_add_duration_unparseable_datetime)

    _run("set_reminder: happy path persists row", test_set_reminder_happy_path_persists_row)
    _run("set_reminder: empty content -> error, no row", test_set_reminder_empty_content_returns_error_persists_nothing)
    _run("set_reminder: bad timestamp -> error, no row", test_set_reminder_bad_timestamp_returns_error_persists_nothing)

    _run("execute_reminder_tool: unknown tool -> None", test_execute_reminder_tool_unknown_returns_none)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
