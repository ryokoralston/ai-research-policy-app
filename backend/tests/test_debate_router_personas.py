"""Tests for routers/debate.py's persona-key validation with custom personas.

Covers: a debate request referencing a valid custom persona key succeeds
(and — critically — that the custom key actually survives into the roster
passed to the background debate task, not just the validation step; see the
DEFAULT_PERSONA_ORDER-based reordering bug this guards against), while an
unknown key still 400s.

_run_debate_task is monkeypatched to a no-op so this test never makes a
real Anthropic API call — it only exercises routers/debate.py's own
validation/persistence logic, same scope as test_users_router.py's
router-level tests.

Run from the backend directory:
    ./venv/bin/python -m tests.test_debate_router_personas
"""
import json
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from models.custom_persona import CustomPersona
from models.debate import Debate


def _make_client_and_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    import routers.debate as debate_router_module

    app = FastAPI()
    app.include_router(debate_router_module.router)
    app.dependency_overrides[get_db] = lambda: db

    async def _noop_run_debate_task(*args, **kwargs):
        return None

    orig = debate_router_module._run_debate_task
    debate_router_module._run_debate_task = _noop_run_debate_task

    return TestClient(app), db, debate_router_module, orig


def _seed_custom_persona(db, key="vp_engineering"):
    db.add(CustomPersona(
        key=key,
        name="Priya Sharma",
        title="VP of Engineering",
        initials="PS",
        color="bg-indigo-600",
        priorities="Ships reliable systems on schedule.",
        style="Blunt and direct.",
        created_by="admin-user-id",
    ))
    db.commit()


def test_debate_with_custom_persona_key_succeeds_and_is_kept():
    client, db, mod, orig = _make_client_and_db()
    try:
        _seed_custom_persona(db)

        resp = client.post("/api/debate/start", json={
            "topic": "Should we adopt a new incident-response process?",
            "persona_keys": ["tech_ceo", "vp_engineering"],
        })
        assert resp.status_code == 200, resp.text
        debate_id = resp.json()["debate_id"]

        debate = db.query(Debate).filter(Debate.id == debate_id).first()
        assert debate is not None
        saved_keys = json.loads(debate.personas)
        # Both the built-in and the custom key must survive — this is the
        # regression the DEFAULT_PERSONA_ORDER-reorder fix guards against
        # (previously any key not in DEFAULT_PERSONA_ORDER was silently
        # dropped here, even though it passed validation).
        assert "tech_ceo" in saved_keys
        assert "vp_engineering" in saved_keys
    finally:
        mod._run_debate_task = orig
        db.close()


def test_debate_with_unknown_persona_key_400s():
    client, db, mod, orig = _make_client_and_db()
    try:
        resp = client.post("/api/debate/start", json={
            "topic": "Should we adopt a new incident-response process?",
            "persona_keys": ["tech_ceo", "not_a_real_key"],
        })
        assert resp.status_code == 400, resp.text
        assert "not_a_real_key" in resp.json()["detail"]
    finally:
        mod._run_debate_task = orig
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
    print("\nRunning debate router persona-validation tests...\n")

    _run("debate with custom persona key succeeds and is kept", test_debate_with_custom_persona_key_succeeds_and_is_kept)
    _run("debate with unknown persona key 400s", test_debate_with_unknown_persona_key_400s)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
