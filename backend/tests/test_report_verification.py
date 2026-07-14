"""Tests for citation/grounding verification integration in report_generator.py.

Covers both generation paths (section-by-section generate_report_stream and
the word-limit _generate_single_pass): verification is skipped gracefully
when source_material is empty, a failure in verify_grounding doesn't break
the main save/complete flow, and a successful result is merged into
Report.metadata_json without clobbering other keys already stored there.

stream_text_with_thinking / verify_grounding are monkeypatched — no API calls.

Run from the backend directory:
    ./venv/bin/python -m tests.test_report_verification
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
from models import Report, ResearchSession
from schemas import ReportGenerateRequest
import services.report_generator as report_generator
import services.report_quality as report_quality

_CONFIDENCE = {
    "confidence_score": 7,
    "unsupported_claims": ["unverified figure in paragraph 2"],
    "notes": "mostly grounded, one flagged figure",
}


async def _fake_stream_text_with_thinking(
    prompt, system="", model=None, max_tokens=8192, cached_context=None, usage_log_tag=None,
):
    yield ("thinking", "considering...")
    yield ("text", "Generated ")
    yield ("text", "section text.")


async def _default_verify_grounding(content, source_material):
    return dict(_CONFIDENCE)


def _make_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_report_with_session(db, existing_metadata_json=None):
    """Create a Report + ResearchSession (with a summary) so _gather_source_material
    returns non-empty source_material via the session_id path."""
    session = ResearchSession(id=str(uuid.uuid4()), query="test query", status="complete",
                               summary="AI systems pose several governance risks.")
    db.add(session)

    report_id = str(uuid.uuid4())
    db.add(Report(id=report_id, title="Test Report", report_type="policy_memo",
                   status="draft", session_id=session.id, metadata_json=existing_metadata_json))
    db.commit()
    return report_id, session.id


def _patch_and_run(db, report_id, request, fake_verify_grounding):
    # report_generator.generate_report_stream now delegates the post-verification
    # revision pass to services.report_quality.revise_if_ungrounded, which resolves
    # stream_text_with_thinking/verify_grounding through report_quality's OWN module
    # globals (not report_generator's) — so both modules' bindings must be patched
    # here, or a grade with non-empty unsupported_claims (see _CONFIDENCE below)
    # would trigger a real, unpatched API call during the revision attempt.
    orig = (
        report_generator.stream_text_with_thinking, report_generator.verify_grounding,
        report_quality.stream_text_with_thinking, report_quality.verify_grounding,
    )
    report_generator.stream_text_with_thinking = _fake_stream_text_with_thinking
    report_generator.verify_grounding = fake_verify_grounding
    report_quality.stream_text_with_thinking = _fake_stream_text_with_thinking
    report_quality.verify_grounding = fake_verify_grounding
    try:
        async def collect():
            return [e async for e in report_generator.generate_report_stream(report_id, request, db)]
        events = asyncio.run(collect())
    finally:
        (report_generator.stream_text_with_thinking, report_generator.verify_grounding,
         report_quality.stream_text_with_thinking, report_quality.verify_grounding) = orig
    report = db.query(Report).filter(Report.id == report_id).first()
    db.refresh(report)
    return report, events


def _build_request(session_id, custom_instructions=None):
    return ReportGenerateRequest(
        report_type="policy_memo", title="Test Report", session_id=session_id,
        custom_instructions=custom_instructions,
    )


# ── generate_report_stream (section-by-section path) ─────────────────────────

def test_verification_success_merges_metadata_without_clobbering():
    db = _make_db()
    report_id, session_id = _make_report_with_session(
        db, existing_metadata_json=json.dumps({"other_key": "keep-me"})
    )
    request = _build_request(session_id)

    report, events = _patch_and_run(db, report_id, request, _default_verify_grounding)

    meta = json.loads(report.metadata_json)
    assert meta["other_key"] == "keep-me", "must not clobber unrelated metadata_json keys"
    assert meta["citation_confidence"] == _CONFIDENCE

    verification_events = [e for e in events if e.startswith("event: verification")]
    assert len(verification_events) == 1
    assert '"confidence_score": 7' in verification_events[0]

    complete = [e for e in events if e.startswith("event: complete")]
    assert len(complete) == 1
    assert '"citation_confidence"' in complete[0]
    assert report.status == "completed"
    assert report.content, "content must be saved"
    db.close()


def test_verification_failure_does_not_break_save_or_complete():
    async def broken_verify(content, source_material):
        raise RuntimeError("judge model unavailable")

    db = _make_db()
    report_id, session_id = _make_report_with_session(db)
    request = _build_request(session_id)

    report, events = _patch_and_run(db, report_id, request, broken_verify)

    assert report.content, "content must still save when verification fails"
    assert report.status == "completed"
    assert report.metadata_json is None, "no metadata should be written on failure"
    assert not any(e.startswith("event: verification") for e in events)
    complete = [e for e in events if e.startswith("event: complete")]
    assert len(complete) == 1, "complete event must still fire"
    db.close()


# ── _generate_single_pass (word-limit path) — direct unit test ───────────────

def test_single_pass_verification_skipped_when_source_material_empty():
    called = {"count": 0}

    async def counting_verify(content, source_material):
        called["count"] += 1
        return dict(_CONFIDENCE)

    db = _make_db()
    report_id = str(uuid.uuid4())
    db.add(Report(id=report_id, title="Empty Source Report", report_type="policy_memo", status="draft"))
    db.commit()

    request = ReportGenerateRequest(
        report_type="policy_memo", title="Empty Source Report",
        custom_instructions="150 words or less",
    )
    system_prompt = "You are a policy memo writer."

    orig = (report_generator.stream_text_with_thinking, report_generator.verify_grounding)
    report_generator.stream_text_with_thinking = _fake_stream_text_with_thinking
    report_generator.verify_grounding = counting_verify
    try:
        async def collect():
            return [
                e async for e in report_generator._generate_single_pass(
                    report_id, request, db, "", system_prompt,
                    report_generator.TEMPLATES["policy_memo"], word_limit=150,
                )
            ]
        events = asyncio.run(collect())
    finally:
        report_generator.stream_text_with_thinking, report_generator.verify_grounding = orig

    assert called["count"] == 0, "verify_grounding must be skipped with empty source_material"
    report = db.query(Report).filter(Report.id == report_id).first()
    db.refresh(report)
    assert report.metadata_json is None
    assert not any(e.startswith("event: verification") for e in events)
    complete = [e for e in events if e.startswith("event: complete")]
    assert len(complete) == 1 and '"citation_confidence": null' in complete[0]
    db.close()


def test_single_pass_verification_success_saved():
    db = _make_db()
    report_id, session_id = _make_report_with_session(db)
    request = _build_request(session_id, custom_instructions="100 words or less")

    report, events = _patch_and_run(db, report_id, request, _default_verify_grounding)

    assert report.metadata_json is not None
    meta = json.loads(report.metadata_json)
    assert meta["citation_confidence"] == _CONFIDENCE
    assert any(e.startswith("event: verification") for e in events)
    complete = [e for e in events if e.startswith("event: complete")]
    assert len(complete) == 1 and '"sections": 1' in complete[0]
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
    print("\nRunning report verification tests...\n")

    _run("verification success merges metadata without clobbering", test_verification_success_merges_metadata_without_clobbering)
    _run("verification failure does not break save/complete", test_verification_failure_does_not_break_save_or_complete)
    _run("single-pass verification skipped when source_material empty", test_single_pass_verification_skipped_when_source_material_empty)
    _run("single-pass verification success saved", test_single_pass_verification_success_saved)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
