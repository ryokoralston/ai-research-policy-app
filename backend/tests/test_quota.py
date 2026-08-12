"""Tests for services/quota.py — the per-user daily cap on billable runs.

Run from the backend directory:
    ./venv/bin/python -m tests.test_quota
"""
import asyncio
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import services.quota as quota
from config import get_settings
from database import Base
from models import RunQuotaCounter
from services.user_service import ROLE_ADMIN, create_user


def _make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_consume_counts_up_to_the_limit():
    db = _make_db()
    user = create_user(db, "reviewer@example.com", "correct-horse-battery-staple")
    for expected in (1, 2, 3):
        assert quota.consume(db, user.id, 3, "research") == expected


def test_consume_raises_429_past_the_limit():
    db = _make_db()
    user = create_user(db, "reviewer@example.com", "correct-horse-battery-staple")
    quota.consume(db, user.id, 2, "research")
    quota.consume(db, user.id, 2, "research")
    try:
        quota.consume(db, user.id, 2, "research")
        raise AssertionError("expected 429 once the daily budget was spent")
    except HTTPException as exc:
        assert exc.status_code == 429, exc.status_code

    # The refusal must not have charged a fourth run.
    row = db.query(RunQuotaCounter).filter_by(user_id=user.id).one()
    assert row.count == 2, row.count


def test_budget_is_per_user():
    db = _make_db()
    alice = create_user(db, "alice@example.com", "correct-horse-battery-staple")
    bob = create_user(db, "bob@example.com", "correct-horse-battery-staple")
    quota.consume(db, alice.id, 1, "research")
    # Alice is out; Bob still has his own full budget.
    try:
        quota.consume(db, alice.id, 1, "research")
        raise AssertionError("expected alice to be capped")
    except HTTPException:
        pass
    assert quota.consume(db, bob.id, 1, "research") == 1


def test_features_share_one_budget():
    """The cap is on spend, not on any single feature, so a user cannot get a
    fresh allowance by switching from research to debates."""
    db = _make_db()
    user = create_user(db, "reviewer@example.com", "correct-horse-battery-staple")
    quota.consume(db, user.id, 2, "research")
    quota.consume(db, user.id, 2, "debate")
    try:
        quota.consume(db, user.id, 2, "report")
        raise AssertionError("expected the third run to be refused")
    except HTTPException as exc:
        assert exc.status_code == 429, exc.status_code


def test_counters_are_scoped_to_the_utc_day():
    db = _make_db()
    user = create_user(db, "reviewer@example.com", "correct-horse-battery-staple")
    quota.consume(db, user.id, 1, "research")

    real_today = quota.today_utc
    quota.today_utc = lambda: "2999-01-01"  # a different day
    try:
        assert quota.consume(db, user.id, 1, "research") == 1
    finally:
        quota.today_utc = real_today


def test_guard_is_a_noop_when_quota_is_unset():
    """Production leaves DEMO_RUN_QUOTA at 0 — no rows, no refusals, ever."""
    db = _make_db()
    user = create_user(db, "reviewer@example.com", "correct-horse-battery-staple")
    get_settings.cache_clear()
    guard = quota.quota_guard("research")
    for _ in range(50):
        asyncio.run(guard(current_user=user, db=db))
    assert db.query(RunQuotaCounter).count() == 0


def test_guard_exempts_admins():
    db = _make_db()
    admin = create_user(db, "owner@example.com", "correct-horse-battery-staple", role=ROLE_ADMIN)
    os.environ["DEMO_RUN_QUOTA"] = "1"
    get_settings.cache_clear()
    try:
        guard = quota.quota_guard("research")
        for _ in range(5):
            asyncio.run(guard(current_user=admin, db=db))
        assert db.query(RunQuotaCounter).count() == 0
    finally:
        del os.environ["DEMO_RUN_QUOTA"]
        get_settings.cache_clear()


def test_guard_enforces_quota_for_members():
    db = _make_db()
    user = create_user(db, "reviewer@example.com", "correct-horse-battery-staple")
    os.environ["DEMO_RUN_QUOTA"] = "2"
    get_settings.cache_clear()
    try:
        guard = quota.quota_guard("research")
        asyncio.run(guard(current_user=user, db=db))
        asyncio.run(guard(current_user=user, db=db))
        try:
            asyncio.run(guard(current_user=user, db=db))
            raise AssertionError("expected the guard to 429 a member over quota")
        except HTTPException as exc:
            assert exc.status_code == 429, exc.status_code
    finally:
        del os.environ["DEMO_RUN_QUOTA"]
        get_settings.cache_clear()


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
    print("\nRunning quota tests...\n")

    _run("consume counts up to the limit", test_consume_counts_up_to_the_limit)
    _run("consume raises 429 past the limit", test_consume_raises_429_past_the_limit)
    _run("budget is per user", test_budget_is_per_user)
    _run("features share one budget", test_features_share_one_budget)
    _run("counters are scoped to the UTC day", test_counters_are_scoped_to_the_utc_day)
    _run("guard is a no-op when quota is unset", test_guard_is_a_noop_when_quota_is_unset)
    _run("guard exempts admins", test_guard_exempts_admins)
    _run("guard enforces quota for members", test_guard_enforces_quota_for_members)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
