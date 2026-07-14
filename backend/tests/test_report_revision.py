"""Tests for services/report_quality.py — the bounded evaluator-optimizer
revision loop that acts on verify_grounding()'s grade.

stream_text_with_thinking / verify_grounding are monkeypatched on the
report_quality module itself (not services.anthropic_client /
services.citation_verifier) — revise_if_ungrounded resolves those names
through report_quality's own module globals, so that's the binding that must
be patched to avoid a real API call. No API calls, no network.

Run from the backend directory:
    ./venv/bin/python -m tests.test_report_revision
"""
import asyncio
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

import services.report_quality as report_quality
from services.report_quality import build_revision_prompt, revise_if_ungrounded

_ORIGINAL_CONTENT = (
    "# Test Report\n\n"
    "## Background\n\nSome background text.\n\n"
    "## Findings\n\nA fabricated statistic: 73% of systems fail.\n"
)
_SOURCE_MATERIAL = "The source material discusses AI governance risks generally."


def _patch(fake_stream, fake_verify):
    orig = (report_quality.stream_text_with_thinking, report_quality.verify_grounding)
    report_quality.stream_text_with_thinking = fake_stream
    report_quality.verify_grounding = fake_verify
    return orig


def _unpatch(orig):
    report_quality.stream_text_with_thinking, report_quality.verify_grounding = orig


def _never_called_stream(*args, **kwargs):
    raise AssertionError("stream_text_with_thinking should not be called")


async def _never_called_verify(content, source_material):
    raise AssertionError("verify_grounding should not be called")


async def _collect(full_content, source_material, first_grade):
    return [
        e async for e in revise_if_ungrounded(
            full_content, source_material, first_grade,
            system_prompt="You write policy reports.",
            cached_context="<source_material>...</source_material>",
            usage_log_tag="report-revision",
        )
    ]


# ── (a) no unsupported claims → no revision attempted ────────────────────────

def test_no_revision_when_first_grade_is_none():
    orig = _patch(_never_called_stream, _never_called_verify)
    try:
        events = asyncio.run(_collect(_ORIGINAL_CONTENT, _SOURCE_MATERIAL, None))
    finally:
        _unpatch(orig)

    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "final"
    assert payload == {"content": _ORIGINAL_CONTENT, "grade": None, "revised": False}


def test_no_revision_when_unsupported_claims_empty():
    first_grade = {"confidence_score": 9, "unsupported_claims": [], "notes": "well grounded"}
    orig = _patch(_never_called_stream, _never_called_verify)
    try:
        events = asyncio.run(_collect(_ORIGINAL_CONTENT, _SOURCE_MATERIAL, first_grade))
    finally:
        _unpatch(orig)

    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "final"
    assert payload == {"content": _ORIGINAL_CONTENT, "grade": first_grade, "revised": False}


def test_no_revision_when_unsupported_claims_key_missing():
    first_grade = {"confidence_score": 9, "notes": "no claims key at all"}
    orig = _patch(_never_called_stream, _never_called_verify)
    try:
        events = asyncio.run(_collect(_ORIGINAL_CONTENT, _SOURCE_MATERIAL, first_grade))
    finally:
        _unpatch(orig)

    assert len(events) == 1
    assert events[0] == ("final", {"content": _ORIGINAL_CONTENT, "grade": first_grade, "revised": False})


# ── (b) claims + re-grade improves → revision accepted ───────────────────────

def test_revision_accepted_when_regrade_improves():
    first_grade = {
        "confidence_score": 5,
        "unsupported_claims": ["73% figure not in source"],
        "notes": "one fabricated statistic",
    }
    revised_text = "# Test Report\n\n## Background\n\nSome background text.\n\n## Findings\n\nFindings supported by source.\n"

    async def fake_stream(prompt, system="", model=None, max_tokens=8192, cached_context=None, usage_log_tag=None):
        yield ("thinking", "checking the flagged claim...")
        yield ("text", "# Test Report\n\n## Background\n\nSome background text.\n\n")
        yield ("text", "## Findings\n\nFindings supported by source.\n")

    async def fake_verify(content, source_material):
        assert content == revised_text
        return {"confidence_score": 8, "unsupported_claims": [], "notes": "now grounded"}

    orig = _patch(fake_stream, fake_verify)
    try:
        events = asyncio.run(_collect(_ORIGINAL_CONTENT, _SOURCE_MATERIAL, first_grade))
    finally:
        _unpatch(orig)

    kinds = [k for k, _ in events]
    assert kinds == ["revision_start", "thinking", "token", "token", "revision_end", "final"], kinds

    start_payload = events[0][1]
    assert start_payload == {"unsupported_claims": first_grade["unsupported_claims"], "confidence_score": 5}

    end_payload = events[-2][1]
    assert end_payload == {"accepted": True, "confidence_score": 8}

    final_kind, final_payload = events[-1]
    assert final_kind == "final"
    assert final_payload["content"] == revised_text
    assert final_payload["grade"] == {"confidence_score": 8, "unsupported_claims": [], "notes": "now grounded"}
    assert final_payload["revised"] is True


# ── (c) claims + re-grade worse → original kept ───────────────────────────────

def test_revision_rejected_when_regrade_is_worse():
    first_grade = {
        "confidence_score": 6,
        "unsupported_claims": ["73% figure not in source"],
        "notes": "one fabricated statistic",
    }

    async def fake_stream(prompt, system="", model=None, max_tokens=8192, cached_context=None, usage_log_tag=None):
        yield ("text", "a worse revision that invents new problems")

    async def fake_verify(content, source_material):
        return {"confidence_score": 2, "unsupported_claims": ["new fabricated claim"], "notes": "worse"}

    orig = _patch(fake_stream, fake_verify)
    try:
        events = asyncio.run(_collect(_ORIGINAL_CONTENT, _SOURCE_MATERIAL, first_grade))
    finally:
        _unpatch(orig)

    end_payload = next(p for k, p in events if k == "revision_end")
    assert end_payload == {"accepted": False, "confidence_score": 6}

    final_kind, final_payload = events[-1]
    assert final_kind == "final"
    assert final_payload == {"content": _ORIGINAL_CONTENT, "grade": first_grade, "revised": False}


# ── (d) revision raises → original kept, final sentinel still emitted ────────

def test_revision_exception_keeps_original_and_still_yields_final():
    first_grade = {
        "confidence_score": 5,
        "unsupported_claims": ["73% figure not in source"],
        "notes": "one fabricated statistic",
    }

    async def raising_stream(prompt, system="", model=None, max_tokens=8192, cached_context=None, usage_log_tag=None):
        if False:  # pragma: no cover - keeps this an async generator function
            yield ("text", "unreachable")
        raise RuntimeError("model unavailable")

    async def unused_verify(content, source_material):
        raise AssertionError("verify_grounding should not be reached if streaming already failed")

    orig = _patch(raising_stream, unused_verify)
    try:
        events = asyncio.run(_collect(_ORIGINAL_CONTENT, _SOURCE_MATERIAL, first_grade))
    finally:
        _unpatch(orig)

    kinds = [k for k, _ in events]
    assert "final" in kinds, "final sentinel must always be yielded, even on failure"

    final_kind, final_payload = events[-1]
    assert final_kind == "final"
    assert final_payload == {"content": _ORIGINAL_CONTENT, "grade": first_grade, "revised": False}


def test_regrade_exception_keeps_original_and_still_yields_final():
    first_grade = {
        "confidence_score": 5,
        "unsupported_claims": ["73% figure not in source"],
        "notes": "one fabricated statistic",
    }

    async def fake_stream(prompt, system="", model=None, max_tokens=8192, cached_context=None, usage_log_tag=None):
        yield ("text", "a revision that streamed fine")

    async def raising_verify(content, source_material):
        raise RuntimeError("judge model unavailable")

    orig = _patch(fake_stream, raising_verify)
    try:
        events = asyncio.run(_collect(_ORIGINAL_CONTENT, _SOURCE_MATERIAL, first_grade))
    finally:
        _unpatch(orig)

    final_kind, final_payload = events[-1]
    assert final_kind == "final"
    assert final_payload == {"content": _ORIGINAL_CONTENT, "grade": first_grade, "revised": False}


# ── (e) build_revision_prompt content ─────────────────────────────────────────

def test_build_revision_prompt_contains_claims_content_and_structure_instruction():
    claims = ["73% figure not in source", "a named individual not mentioned anywhere"]
    prompt = build_revision_prompt(_ORIGINAL_CONTENT, claims, "two fabricated details")

    for claim in claims:
        assert claim in prompt, f"missing claim: {claim}"
    assert _ORIGINAL_CONTENT in prompt
    assert "<unsupported_claims>" in prompt and "</unsupported_claims>" in prompt
    assert "<previous_report>" in prompt and "</previous_report>" in prompt
    # Preserve-structure instruction: must reference both the title line and
    # the section-heading structure.
    assert "# Report Title" in prompt
    assert "## Section" in prompt
    assert "Output ONLY the corrected report" in prompt


def test_build_revision_prompt_omits_notes_block_when_notes_empty():
    prompt = build_revision_prompt(_ORIGINAL_CONTENT, ["a claim"], "")
    assert "<review_notes>" not in prompt


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
    print("\nRunning report revision (evaluator-optimizer loop) tests...\n")

    _run("no revision when first_grade is None", test_no_revision_when_first_grade_is_none)
    _run("no revision when unsupported_claims empty", test_no_revision_when_unsupported_claims_empty)
    _run("no revision when unsupported_claims key missing", test_no_revision_when_unsupported_claims_key_missing)
    _run("revision accepted when regrade improves", test_revision_accepted_when_regrade_improves)
    _run("revision rejected when regrade is worse", test_revision_rejected_when_regrade_is_worse)
    _run("revision exception keeps original + final still yielded", test_revision_exception_keeps_original_and_still_yields_final)
    _run("regrade exception keeps original + final still yielded", test_regrade_exception_keeps_original_and_still_yields_final)
    _run("build_revision_prompt contains claims/content/structure instruction", test_build_revision_prompt_contains_claims_content_and_structure_instruction)
    _run("build_revision_prompt omits notes block when notes empty", test_build_revision_prompt_omits_notes_block_when_notes_empty)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
