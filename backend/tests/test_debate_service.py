"""Tests for the Consensus Meter integration in services.debate_service.run_debate.

stream_text and services.consensus_meter.extract_consensus are monkeypatched —
no API calls. run_debate uses the module-global database.SessionLocal (not a
passed-in db, unlike risk_analyzer/report_generator), so these tests
monkeypatch database.SessionLocal to point at an isolated in-memory
StaticPool-backed engine, matching this repo's existing pattern of faking
service-module attributes rather than calling the real dependencies.

Covers: consensus extraction succeeds → consensus_json saved + "consensus"
SSE event fires + present in "complete" payload; consensus extraction raises
→ debate still completes and synthesis still saves (consensus_json stays
None, no "consensus" event, complete payload's consensus is null) — the
resilience behavior required by this feature.

Run from the backend directory:
    ./venv/bin/python -m tests.test_debate_service
"""
import asyncio
import json
import os
import sys
import uuid

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database as database_module
from database import Base
from models.debate import Debate, DebateArgument  # noqa: F401 — registers tables with Base
import services.consensus_meter as consensus_meter
import services.debate_service as debate_service

_PERSONA_KEYS = ["safety_researcher", "tech_ceo"]

_FAKE_CONSENSUS = {
    "claims": [
        {"claim": "Regulation should precede deployment",
         "stances": {"safety_researcher": "agree", "tech_ceo": "disagree"}},
    ]
}


async def _fake_stream_text(prompt, system="", model=None, max_tokens=8192, temperature=1.0):
    yield "Short "
    yield "argument."


def _make_test_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_debate(session_factory, topic="Should AI be regulated?"):
    db = session_factory()
    debate_id = str(uuid.uuid4())
    db.add(Debate(id=debate_id, topic=topic, status="pending", personas=json.dumps(_PERSONA_KEYS)))
    db.commit()
    db.close()
    return debate_id


def _drain(queue: asyncio.Queue) -> list[str]:
    events = []
    while True:
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return events


def _run_debate_with_patches(fake_extract_consensus):
    session_factory = _make_test_session_factory()
    debate_id = _seed_debate(session_factory)

    orig_session_local = database_module.SessionLocal
    orig_stream_text = debate_service.stream_text
    orig_extract_consensus = consensus_meter.extract_consensus

    database_module.SessionLocal = session_factory
    debate_service.stream_text = _fake_stream_text
    consensus_meter.extract_consensus = fake_extract_consensus

    try:
        queue: asyncio.Queue = asyncio.Queue()
        asyncio.run(debate_service.run_debate(debate_id, "Should AI be regulated?", _PERSONA_KEYS, queue))
        events = _drain(queue)
    finally:
        database_module.SessionLocal = orig_session_local
        debate_service.stream_text = orig_stream_text
        consensus_meter.extract_consensus = orig_extract_consensus

    db = session_factory()
    debate = db.query(Debate).filter(Debate.id == debate_id).first()
    db.refresh(debate)
    return debate, events, db


def test_consensus_success_saved_and_events_fire():
    async def fake_extract(history, synthesis, persona_keys):
        assert persona_keys == _PERSONA_KEYS
        assert synthesis  # non-empty synthesis text was passed
        assert len(history) == len(_PERSONA_KEYS) * 4  # 4 rounds x 2 personas
        return dict(_FAKE_CONSENSUS)

    debate, events, db = _run_debate_with_patches(fake_extract)

    assert debate.status == "complete"
    assert debate.synthesis, "synthesis must still save"
    assert debate.consensus_json is not None
    assert json.loads(debate.consensus_json) == _FAKE_CONSENSUS

    consensus_events = [e for e in events if e.startswith("event: consensus")]
    assert len(consensus_events) == 1
    assert "Regulation should precede deployment" in consensus_events[0]

    complete_events = [e for e in events if e.startswith("event: complete")]
    assert len(complete_events) == 1
    assert "Regulation should precede deployment" in complete_events[0]
    db.close()


def test_consensus_failure_does_not_block_completion():
    async def broken_extract(history, synthesis, persona_keys):
        raise RuntimeError("judge model unavailable")

    debate, events, db = _run_debate_with_patches(broken_extract)

    assert debate.status == "complete", "debate must still complete when consensus extraction fails"
    assert debate.synthesis, "synthesis must still save when consensus extraction fails"
    assert debate.consensus_json is None

    assert not any(e.startswith("event: consensus") for e in events), "no consensus event should fire on failure"
    complete_events = [e for e in events if e.startswith("event: complete")]
    assert len(complete_events) == 1, "complete event must still fire"
    assert '"consensus": null' in complete_events[0]
    assert not any(e.startswith("event: error") for e in events), "failure must not surface as a debate error"
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
    print("\nRunning debate_service consensus integration tests...\n")

    _run("consensus success saved and events fire", test_consensus_success_saved_and_events_fire)
    _run("consensus failure does not block completion", test_consensus_failure_does_not_block_completion)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
