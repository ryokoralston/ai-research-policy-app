"""Tests for the generate_json helper and its two call sites.

generate_json centralizes the prefill('```json') + stop_sequence('```') +
fence-strip + json.loads pattern that research_agent (query decomposition)
and risk_analyzer (score extraction) previously duplicated. Exceptions must
propagate so each call site keeps its own fallback.

Run from the backend directory:
    ./venv/bin/python -m tests.test_generate_json
"""
import asyncio
import os
import sys
import types
import uuid

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

import services.anthropic_client as anthropic_client
import services.research_agent as research_agent


# ── generate_json unit tests ──────────────────────────────────────────────────

def _patch_generate_text(fake):
    original = anthropic_client.generate_text
    anthropic_client.generate_text = fake
    return original


def test_parses_fenced_json():
    captured = {}

    async def fake(prompt, **kwargs):
        captured.update(kwargs)
        # generate_text stitches prefill + generated content together
        return '```json\n["q1", "q2", "q3"]\n'

    original = _patch_generate_text(fake)
    try:
        result = asyncio.run(anthropic_client.generate_json("p", temperature=0.2))
    finally:
        anthropic_client.generate_text = original

    assert result == ["q1", "q2", "q3"], result
    assert captured["prefill"] == "```json"
    assert captured["stop_sequences"] == ["```"]
    assert captured["temperature"] == 0.2


def test_parses_json_object():
    async def fake(prompt, **kwargs):
        return '```json\n{"capability": 7, "misuse": 5}\n'

    original = _patch_generate_text(fake)
    try:
        result = asyncio.run(anthropic_client.generate_json("p"))
    finally:
        anthropic_client.generate_text = original
    assert result == {"capability": 7, "misuse": 5}, result


def test_invalid_json_raises():
    async def fake(prompt, **kwargs):
        return "```json\nnot json at all"

    original = _patch_generate_text(fake)
    try:
        try:
            asyncio.run(anthropic_client.generate_json("p"))
            raise AssertionError("expected a JSON parse error")
        except Exception as exc:
            assert "AssertionError" not in type(exc).__name__
    finally:
        anthropic_client.generate_text = original


# ── research_agent decomposition (success + fallback paths) ──────────────────

class _FakeTavily:
    def __init__(self, *a, **k):
        pass

    async def search(self, query, max_results=5, **kwargs):
        return []  # no sources — pipeline continues with an empty result set


async def _fake_stream_text(prompt, system="", model=None, max_tokens=8192, temperature=1.0):
    yield "synthesis "
    yield "text"


def _run_agent(monkey_generate_json):
    """Run run_research_agent with all externals faked; return (session, events)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from database import Base
    from models import ResearchSession

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    session = ResearchSession(id=str(uuid.uuid4()), query="test query", status="running")
    db.add(session)
    db.commit()

    orig = (research_agent.generate_json, research_agent.TavilyClient, research_agent.stream_text)
    research_agent.generate_json = monkey_generate_json
    research_agent.TavilyClient = _FakeTavily
    research_agent.stream_text = _fake_stream_text
    try:
        queue = asyncio.Queue()
        asyncio.run(research_agent.run_research_agent(
            session_id=session.id, query="test query", max_sources=5, queue=queue, db=db,
        ))
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        research_agent.generate_json, research_agent.TavilyClient, research_agent.stream_text = orig

    db.refresh(session)
    return session, events, db


def test_decomposition_uses_generate_json():
    async def fake_gj(prompt, **kwargs):
        assert "Research question: test query" in prompt
        return ["angle one", "angle two", "angle three"]

    session, events, db = _run_agent(fake_gj)
    queries_events = [e for e in events if e.startswith("event: queries")]
    assert len(queries_events) == 1
    assert "angle one" in queries_events[0]
    assert session.status == "complete", session.status
    db.close()


def test_decomposition_falls_back_to_original_query():
    async def broken_gj(prompt, **kwargs):
        raise RuntimeError("api down")

    session, events, db = _run_agent(broken_gj)
    queries_events = [e for e in events if e.startswith("event: queries")]
    assert len(queries_events) == 1
    assert "test query" in queries_events[0], "fallback must reuse the original query"
    assert session.status == "complete", session.status
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
    print("\nRunning generate_json tests...\n")

    _run("parses fenced JSON array", test_parses_fenced_json)
    _run("parses JSON object", test_parses_json_object)
    _run("invalid JSON raises", test_invalid_json_raises)
    _run("decomposition uses generate_json", test_decomposition_uses_generate_json)
    _run("decomposition falls back to original query", test_decomposition_falls_back_to_original_query)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
