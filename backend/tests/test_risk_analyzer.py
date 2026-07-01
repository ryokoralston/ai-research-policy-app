"""Tests for run_risk_analysis: score extraction via generate_json + fallback.

stream_text / generate_json are monkeypatched — no API calls. Exercises both
the success path (scores event emitted, risk_scores_json saved) and the
failure path (extraction error is logged, analysis still completes).

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


def _run_analysis(fake_generate_json):
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
        context="Some provided context.", run_web_research=False,
    )

    orig = (risk_analyzer.stream_text, risk_analyzer.generate_json)
    risk_analyzer.stream_text = _fake_stream_text
    risk_analyzer.generate_json = fake_generate_json
    try:
        async def collect():
            return [e async for e in risk_analyzer.run_risk_analysis(analysis_id, request, db)]
        events = asyncio.run(collect())
    finally:
        risk_analyzer.stream_text, risk_analyzer.generate_json = orig

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

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
