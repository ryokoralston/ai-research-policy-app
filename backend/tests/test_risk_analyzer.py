"""Tests for run_risk_analysis: score extraction via generate_json + fallback.

stream_text_with_thinking / generate_json are monkeypatched — no API calls. Exercises both
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
from services.risk_analyzer import (
    _build_shared_context,
    _build_section_prompt,
    _build_dimension_revision_prompt,
    WEAK_DIMENSION_THRESHOLD,
    MAX_DIMENSIONS_TO_FIX,
)
from services.tavily_client import SearchResult as TavilySearchResult
from templates import RISK_DIMENSIONS

_SCORES = {"capability": 7, "deployment": 5, "governance": 6,
           "geopolitical": 4, "misuse": 8, "systemic": 5}


async def _fake_stream_text_with_thinking(
    prompt, system="", model=None, max_tokens=8192, cached_context=None, usage_log_tag=None,
):
    yield ("thinking", "reasoning about risk dimensions...")
    yield ("text", "Section content ")
    yield ("text", "for the assessment.")


_DEFAULT_CONFIDENCE = {
    "confidence_score": 8,
    "unsupported_claims": [],
    "notes": "content matches the provided context",
}


async def _default_verify_grounding(content, source_material):
    return dict(_DEFAULT_CONFIDENCE)


class _UncalledTavilyClient:
    """Default TavilyClient stand-in for tests that don't expect any extra-
    research search to happen — raises if .search() is ever invoked, so an
    unexpected Tavily call surfaces as a clear test failure instead of
    silently succeeding against nothing."""

    async def search(self, *args, **kwargs):
        raise AssertionError("TavilyClient.search should not have been called in this test")


def _run_analysis(
    fake_generate_json, fake_verify_grounding=None, context="Some provided context.",
    fake_stream=None, fake_tavily_client_cls=None,
):
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
    fake_stream = fake_stream or _fake_stream_text_with_thinking
    fake_tavily_client_cls = fake_tavily_client_cls or _UncalledTavilyClient

    orig = (
        risk_analyzer.stream_text_with_thinking, risk_analyzer.generate_json,
        risk_analyzer.verify_grounding, risk_analyzer.TavilyClient,
    )
    risk_analyzer.stream_text_with_thinking = fake_stream
    risk_analyzer.generate_json = fake_generate_json
    risk_analyzer.verify_grounding = fake_verify_grounding
    risk_analyzer.TavilyClient = fake_tavily_client_cls
    try:
        async def collect():
            return [e async for e in risk_analyzer.run_risk_analysis(analysis_id, request, db)]
        events = asyncio.run(collect())
    finally:
        (
            risk_analyzer.stream_text_with_thinking, risk_analyzer.generate_json,
            risk_analyzer.verify_grounding, risk_analyzer.TavilyClient,
        ) = orig

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


# ── _build_shared_context / _build_section_prompt (prompt-caching split) ───────
# Pure sanity checks — no monkeypatching, no DB. The shared/cached part must
# hold the (potentially large) research material, the per-section part must
# never repeat it, and the shared part must be byte-identical across repeated
# calls with the same inputs (required for the cache_control breakpoint to
# actually hit across the section loop).

_RESEARCH_MATERIAL = "Frontier model capability evaluations show rapid improvement " * 20


def test_shared_context_contains_research_material():
    shared = _build_shared_context("Autonomous weapons", "technology", _RESEARCH_MATERIAL)
    assert _RESEARCH_MATERIAL[:50] in shared, shared[:200]
    assert "Autonomous weapons" in shared
    assert "technology" in shared


def test_section_prompt_does_not_contain_research_material():
    prompt = _build_section_prompt("Risk Dimensions", "Score each dimension 1-10.")
    assert _RESEARCH_MATERIAL[:50] not in prompt, prompt
    assert "Risk Dimensions" in prompt
    assert "Score each dimension 1-10." in prompt


def test_shared_context_byte_identical_across_calls():
    first = _build_shared_context("Autonomous weapons", "technology", _RESEARCH_MATERIAL)
    second = _build_shared_context("Autonomous weapons", "technology", _RESEARCH_MATERIAL)
    assert first == second, (first, second)


def test_shared_context_omits_tag_when_no_material():
    shared = _build_shared_context("Autonomous weapons", "technology", "")
    assert "<research_material>" not in shared, shared


def test_concatenated_shared_and_section_prompt_matches_pre_split_text():
    """Concatenating the shared context with the per-section prompt must
    reproduce exactly the single prompt string this module built before the
    cached_context split (same text, just partitioned)."""
    shared = _build_shared_context("Autonomous weapons", "technology", "short material")
    per_section = _build_section_prompt("Risk Dimensions", "Score each dimension 1-10.")
    combined = shared + per_section

    expected = (
        "You are conducting a risk assessment of: Autonomous weapons\n"
        "Analysis type: technology\n\n"
        "<research_material>\nshort material\n</research_material>\n\n"
        "Write the 'Risk Dimensions' section of the risk assessment.\n"
        "Instructions: Score each dimension 1-10.\n\n"
        "Write ONLY the section content in Markdown (no header)."
    )
    assert combined == expected, combined


def test_concatenated_shared_and_section_prompt_matches_pre_split_text_no_material():
    """Same reconstruction check, but for the no-source-material branch (the
    <research_material> tag must not appear at all, with no stray blank line
    left behind from its omission)."""
    shared = _build_shared_context("Autonomous weapons", "technology", "")
    per_section = _build_section_prompt("Risk Dimensions", "Score each dimension 1-10.")
    combined = shared + per_section

    expected = (
        "You are conducting a risk assessment of: Autonomous weapons\n"
        "Analysis type: technology\n\n"
        "Write the 'Risk Dimensions' section of the risk assessment.\n"
        "Instructions: Score each dimension 1-10.\n\n"
        "Write ONLY the section content in Markdown (no header)."
    )
    assert combined == expected, combined


# ── Per-dimension weak-grounding re-research loop (_fix_weak_dimensions) ──────
# Covers: skipped entirely with no source material; no-op when all 6
# dimensions grade well; bounded to MAX_DIMENSIONS_TO_FIX when more grade
# weak; accept-on-higher / accept-on-tie / reject-on-lower per the `>=`
# criterion; graceful Tavily-failure and empty-results handling; and the
# pure _build_dimension_revision_prompt helper directly.

_DIMENSION_MARKER = "Assess ONLY the following single risk dimension"
_REVISION_MARKER = "You previously wrote the analysis below for ONE risk dimension"

_ALL_TITLES = [dim["title"] for dim in RISK_DIMENSIONS]


def _title_in(text: str) -> str | None:
    for title in _ALL_TITLES:
        if title in text:
            return title
    return None


def _dimension_token_events(events):
    """Extract the 'text' payload of every 'token' SSE event whose
    section == 'risk_dimensions', in emission order — mirrors
    test_risk_dimensions_parallel.py's helper of the same name."""
    out = []
    for e in events:
        if not e.startswith("event: token"):
            continue
        payload = json.loads(e.split("data: ", 1)[1].strip())
        if payload.get("section") == "risk_dimensions":
            out.append(payload["text"])
    return out


def _dimension_aware_stream(revision_calls: list = None):
    """Build a fake stream_text_with_thinking that recognizes dimension
    prompts and dimension-revision prompts (by their distinctive marker
    prefixes) and emits distinguishable, title-tagged content for each —
    "Original analysis for {title}" for the initial 6 calls, "Revised
    analysis for {title}" for any extra-research revision call. Non-
    dimension section prompts get generic placeholder content.

    If `revision_calls` is given, each dimension-revision call's title is
    appended to it — lets tests assert exactly how many (and which)
    revision generations happened, beyond just inspecting final content."""
    async def fake(prompt, system="", model=None, max_tokens=8192,
                    cached_context=None, usage_log_tag=None):
        if prompt.startswith(_REVISION_MARKER):
            title = _title_in(prompt)
            if revision_calls is not None:
                revision_calls.append(title)
            yield ("text", f"### {title}\nScore: 9/10 (revised)\nRevised analysis for {title}.")
        elif prompt.startswith(_DIMENSION_MARKER):
            title = _title_in(prompt)
            yield ("text", f"### {title}\nScore: 5/10 (ok)\nOriginal analysis for {title}.")
        else:
            yield ("text", "section content")
    return fake


def _fake_verify_grounding_factory(initial_scores: dict, regrade_scores: dict = None, calls: list = None):
    """Build a fake verify_grounding that scores per-dimension content based
    on which dimension title appears in it, distinguishing an original call
    from a re-grade call by the "Revised analysis for" marker
    _dimension_aware_stream's revision output contains. Falls back to a high
    score (10) for content that doesn't match any known title (e.g. the
    final whole-document check, which contains all titles at once — the
    first match wins, which is fine since these tests don't assert on that
    call's score). Appends every call's title (or None) to `calls` if given,
    for call-count/selection assertions."""
    regrade_scores = regrade_scores or {}

    async def fake(content, source_material):
        title = _title_in(content)
        if calls is not None:
            calls.append(title)
        if title is None:
            return {"confidence_score": 10, "unsupported_claims": [], "notes": "n/a"}
        if "Revised analysis for" in content:
            score = regrade_scores.get(title, 10)
        else:
            score = initial_scores.get(title, 10)
        claims = [] if score >= WEAK_DIMENSION_THRESHOLD else [f"unsupported claim about {title}"]
        return {"confidence_score": score, "unsupported_claims": claims, "notes": "test grade"}
    return fake


def _make_tavily_client_cls(search_calls: list, raise_error=False, empty_result=False):
    """Fake TavilyClient — records each search query in `search_calls` for
    assertions, and can simulate either a hard failure (raise_error) or an
    empty-results response (empty_result)."""

    class _FakeTavilyClient:
        async def search(self, query, max_results=3, search_depth="advanced"):
            search_calls.append(query)
            if raise_error:
                raise RuntimeError("tavily down")
            if empty_result:
                return []
            return [TavilySearchResult(
                url="https://example.com/new-evidence",
                title="New Evidence Source",
                snippet="snippet",
                content="Detailed newly-found material directly supporting the flagged claim.",
                score=0.9,
                published_date=None,
            )]
    return _FakeTavilyClient


async def _fake_gj_scores(prompt, **kwargs):
    return dict(_SCORES)


def test_dimension_grading_skipped_without_source_material():
    """No source material (context=None) → the per-dimension grading pass
    must never run at all: verify_grounding and TavilyClient.search are
    never called."""
    verify_calls = []
    fake_verify = _fake_verify_grounding_factory({}, calls=verify_calls)
    search_calls = []
    fake_tavily_cls = _make_tavily_client_cls(search_calls)

    analysis, events, db = _run_analysis(
        _fake_gj_scores, fake_verify_grounding=fake_verify, context=None,
        fake_stream=_dimension_aware_stream(), fake_tavily_client_cls=fake_tavily_cls,
    )
    assert verify_calls == [], "verify_grounding must not be called with no source material"
    assert search_calls == [], "TavilyClient.search must not be called with no source material"
    assert not any(e.startswith("event: status") and "Verifying dimension grounding" in e for e in events)
    db.close()


def test_all_dimensions_well_grounded_no_fixup():
    """All 6 dimensions grade >= WEAK_DIMENSION_THRESHOLD → no Tavily search,
    dimension content unchanged, no extra revision stream calls."""
    initial_scores = {title: 8 for title in _ALL_TITLES}
    fake_verify = _fake_verify_grounding_factory(initial_scores)
    search_calls = []
    fake_tavily_cls = _make_tavily_client_cls(search_calls)
    revision_calls = []

    analysis, events, db = _run_analysis(
        _fake_gj_scores, fake_verify_grounding=fake_verify,
        fake_stream=_dimension_aware_stream(revision_calls), fake_tavily_client_cls=fake_tavily_cls,
    )
    assert search_calls == [], "no dimension is weak — Tavily must not be called"
    assert revision_calls == [], "no dimension is weak — no extra revision generation calls expected"
    blocks = _dimension_token_events(events)
    assert len(blocks) == 6
    for block in blocks:
        assert "Original analysis for" in block, block
        assert "Revised analysis for" not in block, block
    db.close()


def test_more_than_cap_weak_dimensions_only_two_fixed():
    """All 6 dimensions grade weak → only MAX_DIMENSIONS_TO_FIX (2)
    lowest-scoring dimensions get a Tavily search + revision attempt."""
    # Distinct ascending scores, all below threshold, so the two weakest are
    # unambiguous: capability(1) and deployment(2).
    ordered_scores = [1, 2, 3, 4, 5, 5.5]
    assert all(s < WEAK_DIMENSION_THRESHOLD for s in ordered_scores)
    initial_scores = dict(zip(_ALL_TITLES, ordered_scores))
    # Regrade ties initial score so acceptance doesn't matter for this test.
    fake_verify = _fake_verify_grounding_factory(initial_scores, regrade_scores=initial_scores)
    search_calls = []
    fake_tavily_cls = _make_tavily_client_cls(search_calls)

    analysis, events, db = _run_analysis(
        _fake_gj_scores, fake_verify_grounding=fake_verify,
        fake_stream=_dimension_aware_stream(), fake_tavily_client_cls=fake_tavily_cls,
    )
    assert len(search_calls) == MAX_DIMENSIONS_TO_FIX, search_calls
    weakest_two_titles = _ALL_TITLES[:2]  # capability, deployment
    for title in weakest_two_titles:
        assert any(title in q for q in search_calls), (title, search_calls)
    for title in _ALL_TITLES[2:]:
        assert not any(title in q for q in search_calls), (title, search_calls)
    db.close()


def test_weak_dimension_revision_accepted_when_regrade_higher():
    """A weak dimension whose revision scores higher on re-grade → the
    revised content is what appears in the final emitted token events /
    saved analysis.content, not the original."""
    weak_title = _ALL_TITLES[0]
    initial_scores = {weak_title: 3}
    regrade_scores = {weak_title: 9}
    fake_verify = _fake_verify_grounding_factory(initial_scores, regrade_scores=regrade_scores)
    search_calls = []
    fake_tavily_cls = _make_tavily_client_cls(search_calls)

    analysis, events, db = _run_analysis(
        _fake_gj_scores, fake_verify_grounding=fake_verify,
        fake_stream=_dimension_aware_stream(), fake_tavily_client_cls=fake_tavily_cls,
    )
    assert len(search_calls) == 1 and weak_title in search_calls[0]
    blocks = _dimension_token_events(events)
    idx = _ALL_TITLES.index(weak_title)
    assert f"Revised analysis for {weak_title}" in blocks[idx], blocks[idx]
    assert f"Original analysis for {weak_title}" not in blocks[idx], blocks[idx]
    assert f"Revised analysis for {weak_title}" in analysis.content
    for other in _ALL_TITLES:
        if other == weak_title:
            continue
        other_idx = _ALL_TITLES.index(other)
        assert f"Original analysis for {other}" in blocks[other_idx], blocks[other_idx]
    db.close()


def test_weak_dimension_revision_rejected_when_regrade_lower():
    """A weak dimension whose revision scores LOWER on re-grade → rejected;
    the ORIGINAL content is what appears in the final output."""
    weak_title = _ALL_TITLES[0]
    initial_scores = {weak_title: 5}
    regrade_scores = {weak_title: 2}
    fake_verify = _fake_verify_grounding_factory(initial_scores, regrade_scores=regrade_scores)
    search_calls = []
    fake_tavily_cls = _make_tavily_client_cls(search_calls)

    analysis, events, db = _run_analysis(
        _fake_gj_scores, fake_verify_grounding=fake_verify,
        fake_stream=_dimension_aware_stream(), fake_tavily_client_cls=fake_tavily_cls,
    )
    blocks = _dimension_token_events(events)
    idx = _ALL_TITLES.index(weak_title)
    assert f"Original analysis for {weak_title}" in blocks[idx], blocks[idx]
    assert f"Revised analysis for {weak_title}" not in blocks[idx], blocks[idx]
    assert f"Revised analysis for {weak_title}" not in analysis.content
    db.close()


def test_weak_dimension_revision_accepted_when_regrade_equal():
    """A weak dimension whose revision scores EXACTLY EQUAL to the original
    on re-grade → accepted per the `>=` criterion (ties go to the
    revision), same as report_quality.py's revise_if_ungrounded."""
    weak_title = _ALL_TITLES[0]
    tie_score = 4
    initial_scores = {weak_title: tie_score}
    regrade_scores = {weak_title: tie_score}
    fake_verify = _fake_verify_grounding_factory(initial_scores, regrade_scores=regrade_scores)
    search_calls = []
    fake_tavily_cls = _make_tavily_client_cls(search_calls)

    analysis, events, db = _run_analysis(
        _fake_gj_scores, fake_verify_grounding=fake_verify,
        fake_stream=_dimension_aware_stream(), fake_tavily_client_cls=fake_tavily_cls,
    )
    blocks = _dimension_token_events(events)
    idx = _ALL_TITLES.index(weak_title)
    assert f"Revised analysis for {weak_title}" in blocks[idx], blocks[idx]
    db.close()


def test_tavily_search_failure_skips_dimension_gracefully():
    """A weak dimension whose Tavily search raises → gracefully skipped,
    original content kept, no crash."""
    weak_title = _ALL_TITLES[0]
    initial_scores = {weak_title: 2}
    fake_verify = _fake_verify_grounding_factory(initial_scores)
    search_calls = []
    fake_tavily_cls = _make_tavily_client_cls(search_calls, raise_error=True)

    analysis, events, db = _run_analysis(
        _fake_gj_scores, fake_verify_grounding=fake_verify,
        fake_stream=_dimension_aware_stream(), fake_tavily_client_cls=fake_tavily_cls,
    )
    assert len(search_calls) == 1
    blocks = _dimension_token_events(events)
    idx = _ALL_TITLES.index(weak_title)
    assert f"Original analysis for {weak_title}" in blocks[idx], blocks[idx]
    complete = [e for e in events if e.startswith("event: complete")]
    assert len(complete) == 1, "analysis must still complete despite Tavily failure"
    db.close()


def test_tavily_empty_results_skips_dimension_gracefully():
    """A weak dimension whose Tavily search returns no results → gracefully
    skipped, original content kept, no crash."""
    weak_title = _ALL_TITLES[0]
    initial_scores = {weak_title: 2}
    fake_verify = _fake_verify_grounding_factory(initial_scores)
    search_calls = []
    fake_tavily_cls = _make_tavily_client_cls(search_calls, empty_result=True)

    analysis, events, db = _run_analysis(
        _fake_gj_scores, fake_verify_grounding=fake_verify,
        fake_stream=_dimension_aware_stream(), fake_tavily_client_cls=fake_tavily_cls,
    )
    assert len(search_calls) == 1
    blocks = _dimension_token_events(events)
    idx = _ALL_TITLES.index(weak_title)
    assert f"Original analysis for {weak_title}" in blocks[idx], blocks[idx]
    complete = [e for e in events if e.startswith("event: complete")]
    assert len(complete) == 1, "analysis must still complete despite empty Tavily results"
    db.close()


def test_build_dimension_revision_prompt_contains_required_elements():
    """Pure function test — no mocking. The revision prompt must include the
    previous content, the unsupported claims, the new source material, and
    the required output format markers."""
    dimension = RISK_DIMENSIONS[0]
    previous_content = "### Technical Capability Level\nScore: 3/10 (weak)\nSome shaky claims here."
    unsupported_claims = ["Claim that model X scored 99% on benchmark Y with no source"]
    additional_sources = "Official Benchmark Report (https://example.com)\nModel X actually scored 62% on benchmark Y."

    prompt = _build_dimension_revision_prompt(
        dimension, previous_content, unsupported_claims, additional_sources,
    )

    assert previous_content in prompt, prompt
    assert unsupported_claims[0] in prompt, prompt
    assert additional_sources in prompt, prompt
    assert dimension["title"] in prompt
    assert f"### {dimension['title']}" in prompt
    assert "Score: X/10" in prompt


def test_build_dimension_revision_prompt_handles_no_claims():
    """When unsupported_claims is empty, the prompt still renders sensibly
    (a placeholder note instead of an empty bullet list) rather than
    producing malformed output."""
    dimension = RISK_DIMENSIONS[1]
    prompt = _build_dimension_revision_prompt(
        dimension, "previous content here", [], "some new source material",
    )
    assert "previous content here" in prompt
    assert "some new source material" in prompt
    assert dimension["title"] in prompt
    # No claim text to assert on, but must not crash and must still contain
    # a placeholder rather than an empty <unsupported_claims> block.
    assert "<unsupported_claims>" in prompt


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

    _run("shared context contains research material", test_shared_context_contains_research_material)
    _run("section prompt excludes research material", test_section_prompt_does_not_contain_research_material)
    _run("shared context byte-identical across calls", test_shared_context_byte_identical_across_calls)
    _run("shared context omits tag when no material", test_shared_context_omits_tag_when_no_material)
    _run("concatenated shared+section prompt matches pre-split text", test_concatenated_shared_and_section_prompt_matches_pre_split_text)
    _run("concatenated shared+section prompt matches pre-split text (no material)", test_concatenated_shared_and_section_prompt_matches_pre_split_text_no_material)
    _run("verification saved alongside scores", test_verification_saved_alongside_scores)
    _run("verification skipped when no source material", test_verification_skipped_when_no_source_material)
    _run("verification failure does not break main flow", test_verification_failure_does_not_break_main_flow)

    _run("dimension grading skipped without source material", test_dimension_grading_skipped_without_source_material)
    _run("all dimensions well-grounded → no fixup", test_all_dimensions_well_grounded_no_fixup)
    _run("more than cap weak dimensions → only two fixed", test_more_than_cap_weak_dimensions_only_two_fixed)
    _run("weak dimension revision accepted when regrade higher", test_weak_dimension_revision_accepted_when_regrade_higher)
    _run("weak dimension revision rejected when regrade lower", test_weak_dimension_revision_rejected_when_regrade_lower)
    _run("weak dimension revision accepted when regrade equal (tie)", test_weak_dimension_revision_accepted_when_regrade_equal)
    _run("Tavily search failure skips dimension gracefully", test_tavily_search_failure_skips_dimension_gracefully)
    _run("Tavily empty results skips dimension gracefully", test_tavily_empty_results_skips_dimension_gracefully)
    _run("_build_dimension_revision_prompt contains required elements", test_build_dimension_revision_prompt_contains_required_elements)
    _run("_build_dimension_revision_prompt handles no claims", test_build_dimension_revision_prompt_handles_no_claims)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
