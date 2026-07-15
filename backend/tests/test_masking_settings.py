"""Tests for secret masking (utils/masking) and the settings PUT guards.

The '***' sentinel returned by GET must never be persisted as a real value
when a client echoes it back — previously the digest PUT guarded against
this but the model-settings PUT did not.

Run from the backend directory:
    ./venv/bin/python -m tests.test_masking_settings
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from models import model_settings as _ms_models  # noqa: F401 — register tables
from models import digest_settings as _ds_models  # noqa: F401 — register tables
from models.user import User
from services.auth import get_current_user
from utils.masking import MASK, mask_secret

_FAKE_ADMIN = User(id="test-admin", email="admin@example.com", password_hash="x", role="admin")


def _make_client_and_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    from routers.settings import router as settings_router
    from routers.digest import router as digest_router

    app = FastAPI()
    app.include_router(settings_router)
    app.include_router(digest_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: _FAKE_ADMIN
    return TestClient(app), db


# ── mask_secret ───────────────────────────────────────────────────────────────

def test_mask_secret():
    assert mask_secret("sk-ant-xyz") == MASK
    assert mask_secret("") == ""
    assert mask_secret(None) == ""


# ── model settings PUT guard ──────────────────────────────────────────────────

def test_settings_put_ignores_mask_sentinel():
    client, db = _make_client_and_db()
    from models.model_settings import ModelSettings

    resp = client.put("/api/settings/models", json={"anthropic_api_key": "sk-real-key"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["anthropic_api_key"] == MASK  # response is masked

    # Echoing the mask back (as a client re-submitting the GET payload would)
    # must not overwrite the stored key.
    resp = client.put("/api/settings/models", json={"anthropic_api_key": MASK})
    assert resp.status_code == 200, resp.text

    ms = db.query(ModelSettings).first()
    db.refresh(ms)
    assert ms.anthropic_api_key == "sk-real-key", ms.anthropic_api_key
    db.close()


def test_settings_put_empty_string_keeps_existing():
    client, db = _make_client_and_db()
    from models.model_settings import ModelSettings

    client.put("/api/settings/models", json={"openai_api_key": "sk-openai"})
    client.put("/api/settings/models", json={"openai_api_key": ""})

    ms = db.query(ModelSettings).first()
    db.refresh(ms)
    assert ms.openai_api_key == "sk-openai", ms.openai_api_key
    db.close()


def test_settings_get_masks_keys():
    client, db = _make_client_and_db()
    client.put("/api/settings/models", json={"anthropic_api_key": "sk-secret"})
    data = client.get("/api/settings/models").json()
    assert data["anthropic_api_key"] == MASK
    assert "sk-secret" not in str(data)
    db.close()


# ── digest PUT guard (regression — behavior existed before the extraction) ────

def test_digest_put_ignores_mask_sentinel():
    client, db = _make_client_and_db()
    from models.digest_settings import DigestSettings

    resp = client.put("/api/digest/settings", json={"smtp_password": "app-password"})
    assert resp.status_code == 200, resp.text
    resp = client.put("/api/digest/settings", json={"smtp_password": MASK})
    assert resp.status_code == 200, resp.text

    ds = db.query(DigestSettings).first()
    db.refresh(ds)
    assert ds.smtp_password == "app-password", ds.smtp_password
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
    print("\nRunning masking/settings tests...\n")

    _run("mask_secret", test_mask_secret)
    _run("settings PUT ignores mask sentinel", test_settings_put_ignores_mask_sentinel)
    _run("settings PUT empty string keeps existing", test_settings_put_empty_string_keeps_existing)
    _run("settings GET masks keys", test_settings_get_masks_keys)
    _run("digest PUT ignores mask sentinel", test_digest_put_ignores_mask_sentinel)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
