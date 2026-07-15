"""Tests for services/user_service.py — password hashing and user lookups.

Run from the backend directory:
    ./venv/bin/python -m tests.test_user_service
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from services import user_service


def _make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_hash_password_roundtrip():
    h = user_service.hash_password("correct-horse-battery-staple")
    assert h != "correct-horse-battery-staple"
    assert user_service.verify_password("correct-horse-battery-staple", h)
    assert not user_service.verify_password("wrong-password", h)


def test_verify_password_rejects_malformed_hash():
    assert not user_service.verify_password("anything", "not-a-real-bcrypt-hash")


def test_create_user_and_get_by_email():
    db = _make_db()
    user = user_service.create_user(db, "alice@example.com", "hunter2hunter2")
    assert user.role == user_service.ROLE_MEMBER
    assert user.is_active is True
    assert user.password_hash != "hunter2hunter2"

    found = user_service.get_user_by_email(db, "alice@example.com")
    assert found is not None
    assert found.id == user.id
    assert user_service.get_user_by_email(db, "nobody@example.com") is None


def test_create_user_with_admin_role():
    db = _make_db()
    user = user_service.create_user(db, "admin@example.com", "hunter2hunter2", role=user_service.ROLE_ADMIN)
    assert user.role == user_service.ROLE_ADMIN


def test_count_active_admins():
    db = _make_db()
    admin1 = user_service.create_user(db, "a1@example.com", "hunter2hunter2", role=user_service.ROLE_ADMIN)
    admin2 = user_service.create_user(db, "a2@example.com", "hunter2hunter2", role=user_service.ROLE_ADMIN)
    user_service.create_user(db, "m1@example.com", "hunter2hunter2", role=user_service.ROLE_MEMBER)

    assert user_service.count_active_admins(db) == 2
    assert user_service.count_active_admins(db, exclude_user_id=admin1.id) == 1

    admin2.is_active = False
    db.commit()
    assert user_service.count_active_admins(db) == 1


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
    print("\nRunning user_service tests...\n")

    _run("hash_password roundtrip", test_hash_password_roundtrip)
    _run("verify_password rejects malformed hash", test_verify_password_rejects_malformed_hash)
    _run("create_user + get_user_by_email", test_create_user_and_get_by_email)
    _run("create_user with admin role", test_create_user_with_admin_role)
    _run("count_active_admins", test_count_active_admins)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
