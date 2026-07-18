"""Integration test for services.debate_service.run_debate with a custom
persona in the roster — verifies the composed custom-persona system prompt
(from services.persona_service._build_custom_persona_system) actually
reaches stream_text, and that the resulting DebateArgument row carries the
custom persona's persona_name.

Kept as a separate file from tests/test_debate_service.py (which already
covers the Consensus Meter integration in detail) so that file's existing,
carefully-scoped tests stay untouched — same monkeypatch/SessionLocal-faking
pattern as that file, just seeded with a CustomPersona row too.

Run from the backend directory:
    ./venv/bin/python -m tests.test_debate_service_custom_persona
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
from models.custom_persona import CustomPersona
from models.debate import Debate, DebateArgument  # noqa: F401 — registers tables with Base
import services.consensus_meter as consensus_meter
import services.debate_service as debate_service

_CUSTOM_KEY = "vp_engineering"
_PERSONA_KEYS = ["tech_ceo", _CUSTOM_KEY]


def _make_test_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(session_factory, topic="Should we adopt a new incident-response process?"):
    db = session_factory()
    debate_id = str(uuid.uuid4())
    db.add(Debate(id=debate_id, topic=topic, status="pending", personas=json.dumps(_PERSONA_KEYS)))
    db.add(CustomPersona(
        key=_CUSTOM_KEY,
        name="Priya Sharma",
        title="VP of Engineering",
        initials="PS",
        color="bg-indigo-600",
        priorities="Ships reliable systems on schedule and is skeptical of unquantified risk claims.",
        style="Blunt and direct.",
        created_by="admin-user-id",
    ))
    db.commit()
    db.close()
    return debate_id


def test_run_debate_uses_custom_persona_system_prompt():
    session_factory = _make_test_session_factory()
    debate_id = _seed(session_factory)

    captured_systems: list[str] = []

    async def fake_stream_text(prompt, system="", model=None, max_tokens=8192, temperature=1.0):
        captured_systems.append(system)
        yield "Short "
        yield "argument."

    async def fake_extract_consensus(history, synthesis, persona_keys):
        return {"claims": []}  # no claims -> no divergence -> no extra round

    orig_session_local = database_module.SessionLocal
    orig_stream_text = debate_service.stream_text
    orig_extract_consensus = consensus_meter.extract_consensus

    database_module.SessionLocal = session_factory
    debate_service.stream_text = fake_stream_text
    consensus_meter.extract_consensus = fake_extract_consensus

    try:
        queue: asyncio.Queue = asyncio.Queue()
        asyncio.run(debate_service.run_debate(debate_id, "Should we adopt a new incident-response process?", _PERSONA_KEYS, queue))
    finally:
        database_module.SessionLocal = orig_session_local
        debate_service.stream_text = orig_stream_text
        consensus_meter.extract_consensus = orig_extract_consensus

    db = session_factory()
    try:
        debate = db.query(Debate).filter(Debate.id == debate_id).first()
        assert debate.status == "complete"

        custom_args = (
            db.query(DebateArgument)
            .filter(DebateArgument.debate_id == debate_id, DebateArgument.persona_key == _CUSTOM_KEY)
            .all()
        )
        assert len(custom_args) == 4, "custom persona must speak in every one of the 4 fixed rounds"
        assert all(a.persona_name == "Priya Sharma" for a in custom_args)

        # The composed custom-persona system prompt must have actually
        # reached stream_text for this persona's turns.
        custom_systems = [
            s for s in captured_systems
            if "Priya Sharma" in s and "VP of Engineering" in s
        ]
        assert len(custom_systems) == 4, "expected one custom-persona system prompt per round"
        for s in custom_systems:
            assert "Ships reliable systems on schedule" in s
            assert s.endswith("You never start your response by introducing yourself.")
    finally:
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
    print("\nRunning debate_service custom-persona integration test...\n")

    _run("run_debate uses custom persona system prompt", test_run_debate_uses_custom_persona_system_prompt)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
