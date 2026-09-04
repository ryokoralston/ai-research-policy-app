"""Tests for D-3 input bounds (T-06): request size caps via pydantic Field,
`limit` query param caps, and the Anthropic-only model allowlist.

Covers:
  - ResearchStartRequest: max_sources bounds, query length bounds, no more
    `depth` field, unknown `model` id rejected with 400.
  - GET /api/research/?limit=... is capped via Query(ge=1, le=100).
  - PUT /api/settings/models rejects a model id that is neither in the
    model catalog nor the hardcoded fallback list.

The research start endpoint's background task (`_run_research`) is
monkeypatched to a no-op so these tests never make a live Anthropic/Tavily
call — only the request-validation and router-level checks are exercised.

Run from the backend directory:
    ./venv/bin/python -m tests.test_input_bounds
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
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db

# Imported so their tables are registered on Base.metadata before create_all.
from models.model_catalog import ModelCatalogEntry  # noqa: F401
from models.model_settings import ModelSettings  # noqa: F401

from database import get_or_init_model_settings
from schemas.debate import DebateStartRequest
from schemas.document import DocumentAskRequest
from schemas.research import ResearchStartRequest
from services.auth import get_current_user
from services.user_service import ROLE_ADMIN, create_user


def _make_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


async def _noop_run_research(*args, **kwargs):
    """Stands in for services.research_agent.run_research_agent — this test
    only exercises validation and the model-id check, never a real research
    run (no live Anthropic/Tavily calls)."""
    return None


def _make_research_client(db):
    import routers.research as research_router_module

    # Prevent the background task from actually running the research
    # pipeline (which would make live Anthropic/Tavily calls).
    research_router_module._run_research = _noop_run_research

    app = FastAPI()
    app.include_router(research_router_module.router)
    app.dependency_overrides[get_db] = lambda: db

    user = create_user(db, "researcher@example.com", "hunter2hunter2")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _make_settings_client(db):
    from routers.settings import router as settings_router

    app = FastAPI()
    app.include_router(settings_router)
    app.dependency_overrides[get_db] = lambda: db

    admin = create_user(db, "admin@example.com", "hunter2hunter2", role=ROLE_ADMIN)
    app.dependency_overrides[get_current_user] = lambda: admin

    from services.auth import require_admin
    app.dependency_overrides[require_admin] = lambda: admin
    return TestClient(app)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_research_start_rejects_max_sources_over_limit():
    db = _make_db()
    client = _make_research_client(db)
    resp = client.post("/api/research/start", json={"query": "AI policy", "max_sources": 999})
    assert resp.status_code == 422, resp.text


def test_research_start_rejects_query_over_20000_chars():
    db = _make_db()
    client = _make_research_client(db)
    resp = client.post("/api/research/start", json={"query": "a" * 20001})
    assert resp.status_code == 422, resp.text


def test_research_start_accepts_query_at_20000_chars():
    db = _make_db()
    client = _make_research_client(db)
    resp = client.post("/api/research/start", json={"query": "a" * 20000})
    assert resp.status_code != 422, resp.text
    assert resp.status_code == 200, resp.text


def test_research_start_rejects_unknown_model():
    db = _make_db()
    client = _make_research_client(db)
    resp = client.post(
        "/api/research/start",
        json={"query": "AI policy", "model": "not-a-model"},
    )
    assert resp.status_code == 400, resp.text


def test_research_start_accepts_currently_configured_default_model():
    """The research page seeds its model picker from GET /api/settings/models
    and always sends that value explicitly (see frontend/src/app/research/page.tsx).
    ModelSettings.main_model defaults to "claude-opus-4-6", which is not in
    the catalog/fallback allowlist (the catalog only tracks the latest model
    per family, and the fallback list has "claude-opus-5"). Rejecting the
    currently-configured default would 400 the default research flow on any
    deployment whose catalog hasn't refreshed yet — this must still work."""
    db = _make_db()
    ms = get_or_init_model_settings(db)
    client = _make_research_client(db)
    resp = client.post(
        "/api/research/start",
        json={"query": "AI policy", "model": ms.main_model},
    )
    assert resp.status_code == 200, resp.text


def test_research_list_rejects_limit_over_100():
    db = _make_db()
    client = _make_research_client(db)
    resp = client.get("/api/research/?limit=1000")
    assert resp.status_code == 422, resp.text


def test_research_start_request_has_no_depth_field():
    assert "depth" not in ResearchStartRequest.model_fields


def test_settings_put_rejects_unknown_model_id():
    db = _make_db()
    client = _make_settings_client(db)
    resp = client.put("/api/settings/models", json={"main_model": "gpt-4o"})
    assert resp.status_code == 400, resp.text


def test_settings_put_accepts_fallback_model_id():
    db = _make_db()
    client = _make_settings_client(db)
    resp = client.put("/api/settings/models", json={"main_model": "claude-sonnet-5"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["main_model"] == "claude-sonnet-5"


def test_settings_put_accepts_resaving_current_default_model():
    """The settings form echoes the currently-stored main_model / fast_model
    back on every PUT, even when the admin is only rotating the API key (see
    frontend/src/app/settings/page.tsx). Re-saving the stored default (which
    predates the allowlist) must not 400 a routine save."""
    db = _make_db()
    ms = get_or_init_model_settings(db)
    client = _make_settings_client(db)
    resp = client.put(
        "/api/settings/models",
        json={"main_model": ms.main_model, "fast_model": ms.fast_model},
    )
    assert resp.status_code == 200, resp.text


def test_document_ask_rejects_more_than_1000_doc_ids():
    try:
        DocumentAskRequest.model_validate({"question": "q", "doc_ids": ["x"] * 1001})
        raise AssertionError("expected ValidationError for 1001 doc_ids")
    except ValidationError:
        pass
    DocumentAskRequest.model_validate({"question": "q", "doc_ids": ["x"] * 1000})


def test_debate_start_rejects_more_than_50_persona_keys():
    try:
        DebateStartRequest.model_validate({"topic": "t", "persona_keys": ["k"] * 51})
        raise AssertionError("expected ValidationError for 51 persona_keys")
    except ValidationError:
        pass
    DebateStartRequest.model_validate({"topic": "t", "persona_keys": ["k"] * 50})


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
    print("\nRunning input bounds tests...\n")

    _run("research start rejects max_sources over limit", test_research_start_rejects_max_sources_over_limit)
    _run("research start rejects query over 20000 chars", test_research_start_rejects_query_over_20000_chars)
    _run("research start accepts query at 20000 chars", test_research_start_accepts_query_at_20000_chars)
    _run("research start rejects unknown model", test_research_start_rejects_unknown_model)
    _run("research start accepts currently configured default model", test_research_start_accepts_currently_configured_default_model)
    _run("research list rejects limit over 100", test_research_list_rejects_limit_over_100)
    _run("ResearchStartRequest has no depth field", test_research_start_request_has_no_depth_field)
    _run("settings put rejects unknown model id", test_settings_put_rejects_unknown_model_id)
    _run("settings put accepts fallback model id", test_settings_put_accepts_fallback_model_id)
    _run("settings put accepts resaving current default model", test_settings_put_accepts_resaving_current_default_model)
    _run("document ask rejects more than 1000 doc_ids", test_document_ask_rejects_more_than_1000_doc_ids)
    _run("debate start rejects more than 50 persona_keys", test_debate_start_rejects_more_than_50_persona_keys)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
