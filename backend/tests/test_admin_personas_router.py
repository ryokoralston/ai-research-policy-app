"""Tests for routers/admin_personas.py — admin-only custom persona CRUD.

Covers create/list/update/delete, key derivation + collision rejection
(against both built-in PERSONAS and existing custom_personas rows), 404 on
unknown key for PUT/DELETE (which also protects built-in keys, since they
are never rows in this table), and admin-only enforcement (a non-admin
request is rejected by the require_admin dependency itself).

Run from the backend directory:
    ./venv/bin/python -m tests.test_admin_personas_router
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from models.custom_persona import CustomPersona
from services.auth import get_current_user, require_admin
from services.user_service import ROLE_ADMIN, ROLE_MEMBER, create_user

_VALID_BODY = {
    "name": "Priya Sharma",
    "title": "VP of Engineering",
    "initials": "PS",
    "priorities": "Shipping reliable systems on schedule; keeps a close eye on engineering headcount cost.",
    "style": "Blunt and direct; wants concrete numbers before agreeing to anything.",
}


def _make_client_and_db(current_user=None, admin_ok=True):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    from routers.admin_personas import router as admin_personas_router

    app = FastAPI()
    app.include_router(admin_personas_router)
    app.dependency_overrides[get_db] = lambda: db

    user = current_user or create_user(db, "admin@example.com", "hunter2hunter2", role=ROLE_ADMIN)
    app.dependency_overrides[get_current_user] = lambda: user

    if admin_ok:
        app.dependency_overrides[require_admin] = lambda: user
    else:
        def _deny():
            raise HTTPException(status_code=403, detail="Admin access required")
        app.dependency_overrides[require_admin] = _deny

    return TestClient(app), db, user


def test_create_and_list_persona():
    client, db, admin = _make_client_and_db()

    resp = client.post("/api/admin/personas/", json=_VALID_BODY)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"] == "priya_sharma"
    assert body["is_custom"] is True
    assert body["created_by"] == admin.id
    assert body["color"]
    assert body["text_color"]

    listing = client.get("/api/admin/personas/").json()
    keys = {p["key"] for p in listing}
    assert "priya_sharma" in keys


def test_create_rejects_builtin_key_collision():
    client, db, _admin = _make_client_and_db()
    resp = client.post("/api/admin/personas/", json={**_VALID_BODY, "name": "Tech Ceo"})
    assert resp.status_code == 400, resp.text
    assert "built-in" in resp.json()["detail"]


def test_create_rejects_existing_custom_key_collision():
    client, db, _admin = _make_client_and_db()
    resp1 = client.post("/api/admin/personas/", json=_VALID_BODY)
    assert resp1.status_code == 200, resp1.text
    resp2 = client.post("/api/admin/personas/", json=_VALID_BODY)
    assert resp2.status_code == 400, resp2.text


def test_create_rejects_missing_required_fields():
    client, db, _admin = _make_client_and_db()
    resp = client.post("/api/admin/personas/", json={**_VALID_BODY, "priorities": "   "})
    assert resp.status_code == 400, resp.text


def test_update_persona_preserves_key():
    client, db, _admin = _make_client_and_db()
    created = client.post("/api/admin/personas/", json=_VALID_BODY).json()

    resp = client.put(
        f"/api/admin/personas/{created['key']}",
        json={**_VALID_BODY, "title": "SVP of Engineering"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"] == created["key"], "key must stay fixed across renames"
    assert body["title"] == "SVP of Engineering"


def test_update_unknown_key_404s():
    client, db, _admin = _make_client_and_db()
    resp = client.put("/api/admin/personas/does_not_exist", json=_VALID_BODY)
    assert resp.status_code == 404, resp.text


def test_update_builtin_key_404s():
    """PUT on a built-in key must 404 — built-ins were never rows in
    custom_personas, so the ordinary not-found check also protects them."""
    client, db, _admin = _make_client_and_db()
    resp = client.put("/api/admin/personas/tech_ceo", json=_VALID_BODY)
    assert resp.status_code == 404, resp.text


def test_delete_persona():
    client, db, _admin = _make_client_and_db()
    created = client.post("/api/admin/personas/", json=_VALID_BODY).json()

    resp = client.delete(f"/api/admin/personas/{created['key']}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": created["key"]}

    assert db.query(CustomPersona).filter(CustomPersona.key == created["key"]).first() is None


def test_delete_unknown_key_404s():
    client, db, _admin = _make_client_and_db()
    resp = client.delete("/api/admin/personas/does_not_exist")
    assert resp.status_code == 404, resp.text


def test_non_admin_rejected():
    member = None
    client, db, member = _make_client_and_db(admin_ok=False)
    resp = client.post("/api/admin/personas/", json=_VALID_BODY)
    assert resp.status_code == 403, resp.text

    resp = client.get("/api/admin/personas/")
    assert resp.status_code == 403, resp.text


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
    print("\nRunning admin_personas router tests...\n")

    _run("create and list persona", test_create_and_list_persona)
    _run("create rejects builtin key collision", test_create_rejects_builtin_key_collision)
    _run("create rejects existing custom key collision", test_create_rejects_existing_custom_key_collision)
    _run("create rejects missing required fields", test_create_rejects_missing_required_fields)
    _run("update persona preserves key", test_update_persona_preserves_key)
    _run("update unknown key 404s", test_update_unknown_key_404s)
    _run("update builtin key 404s", test_update_builtin_key_404s)
    _run("delete persona", test_delete_persona)
    _run("delete unknown key 404s", test_delete_unknown_key_404s)
    _run("non-admin rejected", test_non_admin_rejected)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
