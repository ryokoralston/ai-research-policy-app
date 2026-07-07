"""Tests for run_risk_analysis: score extraction via generate_json + fallback.

stream_text / generate_json are monkeypatched — no API calls. Exercises both
the success path (scores event emitted, risk_scores_json saved) and the
failure path (extraction error is logged, analysis still completes).

Also covers the citation/grounding verification integration
(services.citation_verifier.verify_grounding): skipped gracefully when
source_material is empty, a failure doesn't break the main save/complete
flow, and a successful result lands in citation_confidence_json + the
verification/complete SSE events without clobbering risk_scores_json.

Run from the backend directory:
    ./venv/bin/python -m tests.test_risk_analyzer
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

from database import Base
from models import RiskAnalysis
from schemas import AnalysisStartRequest
import services.risk_analyzer as risk_analyzer

_SCORES = {"capability": 7, "deployment": 5, "governance": 6,
           "geopolitical": 4, "misuse": 8, "systemic": 5}


async def _fake_stream_text(prompt, system="", model=None, max_tokens=8192, temperature=1.0):
    yield "Section content "
    yield "for the assessment."


_DEFAULT_CONFIDENCE = {
    "confidence_score": 8,
    "unsupported_claims": [],
    "notes": "content matches the provided context",
}


async def _default_verify_grounding(content, source_material):
    return dict(_DEFAULT_CONFIDENCE)


def _run_analysis(fake_generate_json, fake_verify_grounding=None, context="Some provided context."):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    analysis_id = str(uuid.uuid4())
    db.add(RiskAnalysis(id=analysis_id, subject="Test AI system", analysis_type="technology"))
    db.commit()

    request = AnalysisStartRequest(
        subject="Test AI system", analysis_type="technology",
        context=context, run_web_research=False,
    )

    fake_verify_grounding = fake_verify_grounding or _default_verify_grounding

    orig = (risk_analyzer.stream_text, risk_analyzer.generate_json, risk_analyzer.verify_grounding)
    risk_analyzer.stream_text = _fake_stream_text
    risk_analyzer.generate_json = fake_generate_json
    risk_analyzer.verify_grounding = fake_verify_grounding
    try:
        async def collect():
            return [e async for e in risk_analyzer.run_risk_analysis(analysis_id, request, db)]
        events = asyncio.run(collect())
    finally:
        risk_analyzer.stream_text, risk_analyzer.generate_json, risk_analyzer.verify_grounding = orig

    analysis = db.query(RiskAnalysis).filter(RiskAnalysis.id == analysis_id).first()
    db.refresh(analysis)
    return analysis, events, db


def test_scores_extracted_and_saved():
    async def fake_gj(prompt, **kwargs):
        assert "Test AI system" in prompt
        return dict(_SCORES)

    analysis, events, db = _run_analysis(fake_gj)
    assert any(e.startswith("event: scores") for e in events), "scores event expected"
    assert analysis.content and analysis.content.startswith("# Risk Assessment"), analysis.content[:50]
    assert json.loads(analysis.risk_scores_json) == _SCORES
    complete = [e for e in events if e.startswith("event: complete")]
    assert len(complete) == 1 and '"misuse": 8' in complete[0]
    db.close()


def test_verification_saved_alongside_scores():
    """A successful verify_grounding() result must land in citation_confidence_json
    without clobbering risk_scores_json, plus a verification SSE event and the
    citation_confidence key on the complete event."""
    async def fake_gj(prompt, **kwargs):
        return dict(_SCORES)

    analysis, events, db = _run_analysis(fake_gj)
    assert json.loads(analysis.risk_scores_json) == _SCORES, "risk_scores_json must be unaffected"
    assert analysis.citation_confidence_json is not None
    assert json.loads(analysis.citation_confidence_json) == _DEFAULT_CONFIDENCE

    verification_events = [e for e in events if e.startswith("event: verification")]
    assert len(verification_events) == 1
    assert '"confidence_score": 8' in verification_events[0]

    complete = [e for e in events if e.startswith("event: complete")]
    assert len(complete) == 1
    assert '"citation_confidence"' in complete[0]
    assert '"confidence_score": 8' in complete[0]
    db.close()


def test_verification_skipped_when_no_source_material():
    """No context and web research disabled → source_material is empty →
    verify_grounding must not even be called."""
    called = {"count": 0}

    async def counting_verify(content, source_material):
        called["count"] += 1
        return dict(_DEFAULT_CONFIDENCE)

    async def fake_gj(prompt, **kwargs):
        return dict(_SCORES)

    analysis, events, db = _run_analysis(fake_gj, fake_verify_grounding=counting_verify, context=None)
    assert called["count"] == 0, "verify_grounding must be skipped with no source material"
    assert analysis.citation_confidence_json is None
    assert not any(e.startswith("event: verification") for e in events)
    complete = [e for e in events if e.startswith("event: complete")]
    assert len(complete) == 1 and '"citation_confidence": null' in complete[0]
    db.close()


def test_verification_failure_does_not_break_main_flow():
    """A raising verify_grounding() must be caught, logged, and must not prevent
    the analysis content from saving or the complete event from firing."""
    async def broken_verify(content, source_material):
        raise RuntimeError("judge model down")

    async def fake_gj(prompt, **kwargs):
        return dict(_SCORES)

    analysis, events, db = _run_analysis(fake_gj, fake_verify_grounding=broken_verify)
    assert analysis.content, "analysis content must still be saved"
    assert json.loads(analysis.risk_scores_json) == _SCORES, "scores must still save"
    assert analysis.citation_confidence_json is None
    assert not any(e.startswith("event: verification") for e in events)
    complete = [e for e in events if e.startswith("event: complete")]
    assert len(complete) == 1, "complete event must still fire"
    db.close()


def test_extraction_failure_completes_without_scores():
    async def broken_gj(prompt, **kwargs):
        raise RuntimeError("api down")

    analysis, events, db = _run_analysis(broken_gj)
    assert not any(e.startswith("event: scores") for e in events)
    assert analysis.content, "analysis content must still be saved"
    assert analysis.risk_scores_json is None
    assert any(e.startswith("event: complete") for e in events)
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
    print("\nRunning risk analyzer tests...\n")

    _run("scores extracted and saved", test_scores_extracted_and_saved)
    _run("extraction failure completes without scores", test_extraction_failure_completes_without_scores)
    _run("verification saved alongside scores", test_verification_saved_alongside_scores)
    _run("verification skipped when no source material", test_verification_skipped_when_no_source_material)
    _run("verification failure does not break main flow", test_verification_failure_does_not_break_main_flow)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
