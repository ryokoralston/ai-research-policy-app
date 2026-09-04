"""Tests for services/usage.py — the provider-spend recorder.

Run from the backend directory:
    ./venv/bin/python -m tests.test_usage
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from cryptography.fernet import Fernet

os.environ.setdefault("SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
from database import Base
from models.usage_event import UsageEvent
from services import usage


class _FakeUsage:
    """Stands in for the SDK's usage object."""

    def __init__(self, **fields):
        for key, value in fields.items():
            setattr(self, key, value)


def _install_db():
    """Point services.usage at a fresh in-memory database.

    usage._record imports SessionLocal from `database` at call time, so
    replacing the module attribute is enough — no need to reach into the
    service.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    database.SessionLocal = factory
    return factory


def _rows(factory):
    with factory() as db:
        return db.query(UsageEvent).all()


# ── recording ─────────────────────────────────────────────────────────────────

def test_records_anthropic_call_with_context():
    factory = _install_db()
    with usage.usage_context(user_id="u1", feature="research"):
        usage.record_anthropic(
            "claude-opus-5",
            _FakeUsage(
                input_tokens=100,
                output_tokens=20,
                cache_read_input_tokens=5,
                cache_creation_input_tokens=7,
            ),
        )
    rows = _rows(factory)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row.user_id == "u1"
    assert row.feature == "research"
    assert row.provider == "anthropic"
    assert row.model == "claude-opus-5"
    assert row.input_tokens == 100
    assert row.output_tokens == 20
    assert row.cache_read_input_tokens == 5
    assert row.cache_creation_input_tokens == 7
    assert row.request_count == 1


def test_records_tavily_search():
    factory = _install_db()
    with usage.usage_context(user_id="u1", feature="research"):
        usage.record_tavily(1)
    rows = _rows(factory)
    assert len(rows) == 1
    assert rows[0].provider == "tavily"
    assert rows[0].model is None
    assert rows[0].request_count == 1
    assert rows[0].input_tokens == 0


def test_no_context_records_nothing():
    """A provider call outside any entry point must not create an
    unattributable row."""
    factory = _install_db()
    usage.record_anthropic("claude-opus-5", _FakeUsage(input_tokens=10, output_tokens=1))
    assert _rows(factory) == []


def test_context_is_restored_after_block():
    factory = _install_db()
    with usage.usage_context(user_id="u1", feature="research"):
        with usage.usage_context(user_id="u2", feature="report"):
            usage.record_anthropic("m", _FakeUsage(input_tokens=1))
        usage.record_anthropic("m", _FakeUsage(input_tokens=2))
    rows = sorted(_rows(factory), key=lambda r: r.input_tokens)
    assert [(r.user_id, r.feature) for r in rows] == [("u2", "report"), ("u1", "research")]


def test_missing_usage_fields_default_to_zero():
    """The SDK's usage object doesn't carry every field on every endpoint."""
    factory = _install_db()
    with usage.usage_context(user_id="u1", feature="qa"):
        usage.record_anthropic("m", _FakeUsage(input_tokens=3))
    row = _rows(factory)[0]
    assert row.input_tokens == 3
    assert row.output_tokens == 0
    assert row.cache_read_input_tokens == 0


def test_null_user_is_allowed():
    """Scheduled work (the digest) runs with no user in context."""
    factory = _install_db()
    with usage.usage_context(user_id=None, feature="digest"):
        usage.record_anthropic("m", _FakeUsage(input_tokens=1))
    assert _rows(factory)[0].user_id is None


def test_none_usage_is_ignored():
    """Not every response carries a usage object — that must cost a row, not
    the request."""
    factory = _install_db()
    with usage.usage_context(user_id="u1", feature="research"):
        usage.record_anthropic("m", None)  # must not raise
    assert _rows(factory) == []


def test_report_endpoint_records_usage_event():
    """routers/reports.py wraps the SSE generator body in usage_context — a
    stubbed generate_report_stream that records an Anthropic call must land
    exactly one UsageEvent row, attributed to the authenticated caller and
    feature "report" (see routers/reports.py's event_generator)."""
    factory = _install_db()

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from database import get_db
    from models.user import User
    from services.auth import get_current_user
    from routers.reports import router as reports_router

    db = factory()
    user = User(
        id="u-report-test",
        email="report-test@example.com",
        password_hash="x",
        role="member",
    )
    db.add(user)
    db.commit()

    app = FastAPI()
    app.include_router(reports_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user

    async def _fake_generate_report_stream(report_id, request, db):
        usage.record_anthropic("claude-x", _FakeUsage(input_tokens=3, output_tokens=4))
        yield "data: {}\n\n"

    import services.report_generator
    original = services.report_generator.generate_report_stream
    services.report_generator.generate_report_stream = _fake_generate_report_stream
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/reports/generate",
            json={"report_type": "policy_memo", "title": "Test report"},
        )
        assert resp.status_code == 200, resp.text
        # Force the streaming body to run to completion.
        list(resp.iter_lines())
    finally:
        services.report_generator.generate_report_stream = original

    rows = _rows(factory)
    assert len(rows) == 1, rows
    assert rows[0].feature == "report"
    assert rows[0].user_id == "u-report-test"


def test_debate_task_records_usage_event():
    """_run_debate_task wraps run_debate in usage_context(user_id=..., feature=
    "debate") — called directly here since it's a plain background-task
    coroutine, not something a TestClient can drive."""
    factory = _install_db()

    import asyncio
    import services.debate_service
    from routers.debate import _run_debate_task

    async def _fake_run_debate(debate_id, topic, persona_keys, queue):
        usage.record_anthropic("claude-x", _FakeUsage(input_tokens=1, output_tokens=1))

    original = services.debate_service.run_debate
    services.debate_service.run_debate = _fake_run_debate
    try:
        asyncio.run(_run_debate_task("d1", "topic", [], asyncio.Queue(), "u1"))
    finally:
        services.debate_service.run_debate = original

    rows = _rows(factory)
    assert len(rows) == 1, rows
    assert rows[0].feature == "debate"
    assert rows[0].user_id == "u1"


def test_recording_failure_does_not_raise():
    """Accounting must never break the request it is measuring."""
    _install_db()

    class _Boom:
        def __call__(self, *a, **kw):
            raise RuntimeError("db is down")

    original = database.SessionLocal
    database.SessionLocal = _Boom()
    try:
        with usage.usage_context(user_id="u1", feature="research"):
            usage.record_anthropic("m", _FakeUsage(input_tokens=1))  # must not raise
    finally:
        database.SessionLocal = original


def test_context_reset_from_foreign_context():
    """When an async generator using usage_context() is finalized from a
    different context (e.g., when an HTTP client disconnects mid-stream and
    aclose() runs in a different asyncio task), ContextVar.reset() should not
    raise ValueError. The usage row is already written before the yield, so
    there is no data loss or misattribution."""
    import asyncio
    import contextvars

    factory = _install_db()

    async def scenario():
        async def gen():
            with usage.usage_context(user_id="u", feature="f"):
                usage.record_anthropic("m", _FakeUsage(input_tokens=1))
                yield 1
                yield 2

        g = gen()
        # Enter the with-block in this task's context.
        first_val = await g.__anext__()
        assert first_val == 1

        # Finalize from a fresh context, as an asyncgen finalizer would
        # when the HTTP client disconnects mid-stream.
        # create_task(g.aclose()) copies the current context → different Context object
        await asyncio.create_task(g.aclose())  # must not raise ValueError

        # Usage row was written before the yield, so it exists.
        rows = _rows(factory)
        assert len(rows) == 1
        assert rows[0].user_id == "u"
        assert rows[0].feature == "f"

    asyncio.run(scenario())


# ── runner ────────────────────────────────────────────────────────────────────

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
    print("\nRunning usage tests...\n")

    _run("records anthropic call with context", test_records_anthropic_call_with_context)
    _run("records tavily search", test_records_tavily_search)
    _run("no context records nothing", test_no_context_records_nothing)
    _run("context is restored after block", test_context_is_restored_after_block)
    _run("missing usage fields default to zero", test_missing_usage_fields_default_to_zero)
    _run("null user is allowed", test_null_user_is_allowed)
    _run("none usage is ignored", test_none_usage_is_ignored)
    _run("report endpoint records usage event", test_report_endpoint_records_usage_event)
    _run("debate task records usage event", test_debate_task_records_usage_event)
    _run("recording failure does not raise", test_recording_failure_does_not_raise)
    _run("context reset from foreign context", test_context_reset_from_foreign_context)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
