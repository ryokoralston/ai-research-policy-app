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
resilience behavior required by this feature. Also covers the bounded
"extra round" evaluator-optimizer extension: a genuinely-split first-pass
consensus triggers exactly one extra debate round + re-synthesis +
re-extraction, and a failure on that second extraction keeps the original
first-pass synthesis/consensus rather than nulling them out. Plus direct
input/output tests for the pure helpers (_consensus_divergence_score,
_select_most_contested_claim) that decide whether/where the extra round
fires.

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

# Both personas agree here — divergence_score is 0.0, so this claim set must
# NOT trigger the bounded extra round (see CONSENSUS_DIVERGENCE_THRESHOLD in
# debate_service.py). Kept non-divergent on purpose so
# test_consensus_success_saved_and_events_fire continues to exercise ONLY the
# baseline (non-extra-round) path, as originally intended — the extra-round
# path has its own dedicated tests below.
_FAKE_CONSENSUS = {
    "claims": [
        {"claim": "Regulation should precede deployment",
         "stances": {"safety_researcher": "agree", "tech_ceo": "agree"}},
    ]
}

# A genuinely split claim set: one persona agrees, the other disagrees, on
# the debate's only extracted claim — 1/1 claims split, divergence_score is
# 1.0, well above CONSENSUS_DIVERGENCE_THRESHOLD (0.5). Used by the
# extra-round tests below.
_FAKE_CONSENSUS_SPLIT = {
    "claims": [
        {"claim": "Regulation should precede deployment",
         "stances": {"safety_researcher": "agree", "tech_ceo": "disagree"}},
    ]
}

# The claim set returned by a *second* extract_consensus call (post-extra-
# round) that converged — used to verify the final saved/emitted consensus
# reflects the second call, not the first.
_FAKE_CONSENSUS_CONVERGED = {
    "claims": [
        {"claim": "Export controls need international coordination",
         "stances": {"safety_researcher": "agree", "tech_ceo": "agree"}},
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


def test_extra_round_runs_on_genuine_divergence():
    """First extract_consensus call returns a genuinely-split claim set (see
    _FAKE_CONSENSUS_SPLIT) — this must trigger the bounded extra round: a 5th
    debate round targeted at the contested claim, a re-synthesis, and a
    second extract_consensus call whose result supersedes the first."""
    call_count = [0]

    async def fake_extract(history, synthesis, persona_keys):
        call_count[0] += 1
        if call_count[0] == 1:
            return dict(_FAKE_CONSENSUS_SPLIT)
        return dict(_FAKE_CONSENSUS_CONVERGED)

    debate, events, db = _run_debate_with_patches(fake_extract)

    assert call_count[0] == 2, "extract_consensus must be called exactly twice"

    assert debate.status == "complete"
    assert debate.synthesis, "synthesis must still save"
    assert debate.consensus_json is not None
    # Final saved consensus reflects the SECOND call's result, not the first.
    assert json.loads(debate.consensus_json) == _FAKE_CONSENSUS_CONVERGED

    round5_args = (
        db.query(DebateArgument)
        .filter(DebateArgument.debate_id == debate.id, DebateArgument.round_number == 5)
        .all()
    )
    assert len(round5_args) == len(_PERSONA_KEYS), "extra round must save one argument per persona"

    round_start_5 = [
        e for e in events
        if e.startswith("event: round_start") and '"round": 5' in e
    ]
    assert len(round_start_5) == 1, "a round_start event for round 5 must fire"

    resynthesis_events = [e for e in events if e.startswith("event: resynthesis_start")]
    assert len(resynthesis_events) == 1, "resynthesis_start must fire before the extra round's synthesis"

    complete_events = [e for e in events if e.startswith("event: complete")]
    assert len(complete_events) == 1
    assert "Export controls need international coordination" in complete_events[0]
    assert "Regulation should precede deployment" not in complete_events[0]
    db.close()


def test_extra_round_second_consensus_call_fails_keeps_original():
    """If the first pass is genuinely split (triggering the extra round) but
    the SECOND extract_consensus call raises, the debate must still complete
    with the ORIGINAL (first-pass) synthesis and consensus kept — not null,
    not crashed. Same graceful-degradation posture as a first-pass failure."""
    call_count = [0]

    async def flaky_extract(history, synthesis, persona_keys):
        call_count[0] += 1
        if call_count[0] == 1:
            return dict(_FAKE_CONSENSUS_SPLIT)
        raise RuntimeError("judge model unavailable on second pass")

    debate, events, db = _run_debate_with_patches(flaky_extract)

    assert call_count[0] == 2

    assert debate.status == "complete", "debate must still complete when the second consensus call fails"
    assert debate.synthesis, "synthesis must still save"
    assert debate.consensus_json is not None
    assert json.loads(debate.consensus_json) == _FAKE_CONSENSUS_SPLIT, (
        "must keep the ORIGINAL first-pass consensus, not null it out"
    )

    complete_events = [e for e in events if e.startswith("event: complete")]
    assert len(complete_events) == 1
    assert "Regulation should precede deployment" in complete_events[0]

    assert not any(e.startswith("event: error") for e in events), "failure must not surface as a debate error"
    db.close()


def test_divergence_score_pure_helper():
    assert debate_service._consensus_divergence_score([]) == 0.0

    no_split = [
        {"claim": "A", "stances": {"x": "agree", "y": "agree"}},
        {"claim": "B", "stances": {"x": "mixed", "y": "mixed"}},
    ]
    assert debate_service._consensus_divergence_score(no_split) == 0.0

    one_split = [
        {"claim": "A", "stances": {"x": "agree", "y": "disagree"}},
        {"claim": "B", "stances": {"x": "agree", "y": "agree"}},
    ]
    assert debate_service._consensus_divergence_score(one_split) == 0.5

    all_split = [
        {"claim": "A", "stances": {"x": "agree", "y": "disagree"}},
        {"claim": "B", "stances": {"x": "disagree", "y": "agree", "z": "mixed"}},
    ]
    assert debate_service._consensus_divergence_score(all_split) == 1.0

    # "mixed" stances alone (no actual agree/disagree pair) must not count as
    # a split, per consensus_meter.py's docstring: mixed is the neutral
    # fallback for personas that never addressed a claim.
    only_mixed = [{"claim": "A", "stances": {"x": "mixed", "y": "mixed"}}]
    assert debate_service._consensus_divergence_score(only_mixed) == 0.0


def test_select_most_contested_claim_pure_helper():
    assert debate_service._select_most_contested_claim([]) is None

    no_qualifying = [
        {"claim": "A", "stances": {"x": "agree", "y": "agree"}},
        {"claim": "B", "stances": {"x": "mixed", "y": "mixed"}},
    ]
    assert debate_service._select_most_contested_claim(no_qualifying) is None

    single = [
        {"claim": "A", "stances": {"x": "agree", "y": "disagree"}},
        {"claim": "B", "stances": {"x": "agree", "y": "agree"}},
    ]
    result = debate_service._select_most_contested_claim(single)
    assert result is not None and result["claim"] == "A"

    # "B" is more evenly split (2 agree / 2 disagree, min=2) than "A" (3
    # agree / 1 disagree, min=1) — the most EVEN split must win, not the one
    # with more total votes.
    multi = [
        {"claim": "A", "stances": {"p1": "agree", "p2": "agree", "p3": "agree", "p4": "disagree"}},
        {"claim": "B", "stances": {"p1": "agree", "p2": "agree", "p3": "disagree", "p4": "disagree"}},
    ]
    result = debate_service._select_most_contested_claim(multi)
    assert result is not None and result["claim"] == "B"


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
    _run("extra round runs on genuine divergence", test_extra_round_runs_on_genuine_divergence)
    _run("extra round second consensus call fails keeps original", test_extra_round_second_consensus_call_fails_keeps_original)
    _run("divergence score pure helper", test_divergence_score_pure_helper)
    _run("select most contested claim pure helper", test_select_most_contested_claim_pure_helper)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
