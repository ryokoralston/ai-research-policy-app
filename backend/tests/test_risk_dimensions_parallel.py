"""Tests for the 6-way parallel "risk_dimensions" analysis path in
run_risk_analysis: _build_dimension_prompt, _analyze_dimension, and the
asyncio.create_task fan-out / await-in-canonical-order fan-in that replaced
the single crammed-together risk_dimensions prompt.

stream_text_with_thinking / generate_json are monkeypatched — no API calls,
mirroring test_risk_analyzer.py's pattern (fake db, module-attribute
monkeypatching, restore in `finally`).

(a) "exactly 6 calls, each isolated to its own title" and (e) "prompt
contains scale + criteria" are checked directly against the pure helpers.
(b) concurrency, (c) out-of-order completion / canonical emission order, and
(d) one-dimension-failure isolation all drive the real orchestration code
path via run_risk_analysis, since they depend on the actual
asyncio.create_task fan-out inside the "risk_dimensions" branch of the
section loop — a fake that bypasses run_risk_analysis couldn't exercise
that.

Run from the backend directory:
    ./venv/bin/python -m tests.test_risk_dimensions_parallel
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
from services.risk_analyzer import _build_dimension_prompt, RISK_DIMENSIONS

# Unique prefix _build_dimension_prompt always emits — lets fakes tell a
# per-dimension call apart from the other 5 (non-risk_dimensions) sections'
# calls to the same stream_text_with_thinking name.
_DIMENSION_MARKER = "Assess ONLY the following single risk dimension"


def _is_dimension_prompt(prompt: str) -> bool:
    return prompt.startswith(_DIMENSION_MARKER)


def _title_for_prompt(prompt: str) -> str:
    """Identify which RISK_DIMENSIONS entry a dimension prompt belongs to by
    its (pairwise non-overlapping) title substring."""
    for dim in RISK_DIMENSIONS:
        if dim["title"] in prompt:
            return dim["title"]
    raise AssertionError(f"no known dimension title found in prompt: {prompt[:200]!r}")


async def _fake_generate_json(prompt, **kwargs):
    """Fixed scores payload — these tests care about the risk_dimensions
    streaming path, not score extraction, so keep this a no-op success."""
    return {"capability": 5, "deployment": 5, "governance": 5,
            "geopolitical": 5, "misuse": 5, "systemic": 5}


def _run(fake_stream):
    """Drive run_risk_analysis end to end with stream_text_with_thinking and
    generate_json monkeypatched — same shape as test_risk_analyzer.py's
    _run_analysis helper. context=None (and run_web_research=False) so
    citation verification is skipped entirely and doesn't need faking.
    Returns (events, analysis)."""
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
        context=None, run_web_research=False,
    )

    orig = (risk_analyzer.stream_text_with_thinking, risk_analyzer.generate_json)
    risk_analyzer.stream_text_with_thinking = fake_stream
    risk_analyzer.generate_json = _fake_generate_json
    try:
        async def collect():
            return [e async for e in risk_analyzer.run_risk_analysis(analysis_id, request, db)]
        events = asyncio.run(collect())
    finally:
        risk_analyzer.stream_text_with_thinking, risk_analyzer.generate_json = orig

    analysis = db.query(RiskAnalysis).filter(RiskAnalysis.id == analysis_id).first()
    db.refresh(analysis)
    db.close()
    return events, analysis


def _dimension_token_events(events):
    """Extract the 'text' payload of every 'token' SSE event whose
    section == 'risk_dimensions', in emission order — one entry per
    dimension block, per the run_risk_analysis parallel-path contract."""
    out = []
    for e in events:
        if not e.startswith("event: token"):
            continue
        payload = json.loads(e.split("data: ", 1)[1].strip())
        if payload.get("section") == "risk_dimensions":
            out.append(payload["text"])
    return out


# ── (a) exactly 6 dimension calls, each prompt isolated to its own title ────

def test_exactly_six_dimension_calls_with_isolated_titles():
    calls = []

    async def counting_fake(prompt, system="", model=None, max_tokens=8192,
                             cached_context=None, usage_log_tag=None):
        calls.append(prompt)
        if _is_dimension_prompt(prompt):
            title = _title_for_prompt(prompt)
            yield ("text", f"### {title}\nScore: 5/10 (ok)\nAnalysis for {title}.")
        else:
            yield ("text", "section content")

    _run(counting_fake)

    dimension_calls = [p for p in calls if _is_dimension_prompt(p)]
    assert len(dimension_calls) == 6, f"expected 6 dimension calls, got {len(dimension_calls)}"

    all_titles = [dim["title"] for dim in RISK_DIMENSIONS]
    for prompt in dimension_calls:
        mentioned = [t for t in all_titles if t in prompt]
        assert mentioned and len(mentioned) == 1, (
            f"prompt should mention exactly one dimension title, mentioned {mentioned}: {prompt[:300]!r}"
        )


# ── (b) true concurrency: all 6 dimension calls in flight simultaneously ────

def test_six_dimension_calls_run_concurrently():
    state = {"in_flight": 0, "max_in_flight": 0}
    all_entered = asyncio.Event()

    async def concurrency_fake(prompt, system="", model=None, max_tokens=8192,
                                cached_context=None, usage_log_tag=None):
        if not _is_dimension_prompt(prompt):
            yield ("text", "section content")
            return
        state["in_flight"] += 1
        state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        if state["in_flight"] >= 6:
            # Last of the 6 to enter — release everyone staggered behind
            # asyncio.sleep(0)-equivalent suspension (Event.wait()).
            all_entered.set()
        else:
            await all_entered.wait()
        yield ("text", "### dim\nScore: 5/10 (ok)\ncontent")
        state["in_flight"] -= 1

    _run(concurrency_fake)

    assert state["max_in_flight"] == 6, (
        f"expected all 6 dimension calls in flight at once, got max_in_flight={state['max_in_flight']}"
    )


# ── (c) out-of-order completion still emits/assembles in canonical order ────

def test_out_of_order_completion_preserves_canonical_emission_order():
    # Reverse-order delays: RISK_DIMENSIONS[0] sleeps longest,
    # RISK_DIMENSIONS[-1] returns fastest — so completion order is the
    # REVERSE of canonical order, while the SSE "token" events (and the
    # assembled section_content) must still follow RISK_DIMENSIONS' order.
    n = len(RISK_DIMENSIONS)
    delays = {dim["title"]: (n - 1 - i) * 0.02 for i, dim in enumerate(RISK_DIMENSIONS)}

    async def staggered_fake(prompt, system="", model=None, max_tokens=8192,
                              cached_context=None, usage_log_tag=None):
        if not _is_dimension_prompt(prompt):
            yield ("text", "section content")
            return
        title = _title_for_prompt(prompt)
        await asyncio.sleep(delays[title])
        yield ("text", f"### {title}\nScore: 5/10 (ok)\nAnalysis for {title}.")

    events, analysis = _run(staggered_fake)

    blocks = _dimension_token_events(events)
    assert len(blocks) == 6, blocks
    for i, dim in enumerate(RISK_DIMENSIONS):
        assert dim["title"] in blocks[i], (
            f"expected emitted block {i} to be dimension {dim['title']!r}, got: {blocks[i][:120]!r}"
        )

    # The assembled, saved content must also preserve canonical order.
    positions = [analysis.content.find(dim["title"]) for dim in RISK_DIMENSIONS]
    assert all(p != -1 for p in positions), positions
    assert positions == sorted(positions), (
        f"dimension titles are not in canonical order in saved content: {positions}"
    )


# ── (d) one failing dimension → placeholder; other five unaffected ──────────

def test_one_dimension_failure_yields_placeholder_others_intact():
    failing_title = RISK_DIMENSIONS[2]["title"]  # arbitrary middle dimension

    async def flaky_fake(prompt, system="", model=None, max_tokens=8192,
                          cached_context=None, usage_log_tag=None):
        if not _is_dimension_prompt(prompt):
            yield ("text", "section content")
            return
        title = _title_for_prompt(prompt)
        if title == failing_title:
            raise RuntimeError("simulated API failure")
        yield ("text", f"### {title}\nScore: 5/10 (ok)\nAnalysis for {title}.")

    # Must not raise — one failing dimension must not sink the other five or
    # the analysis as a whole.
    events, analysis = _run(flaky_fake)

    blocks = _dimension_token_events(events)
    assert len(blocks) == 6, blocks

    for i, dim in enumerate(RISK_DIMENSIONS):
        if dim["title"] == failing_title:
            assert "Analysis unavailable" in blocks[i], blocks[i]
            assert dim["title"] in blocks[i]
        else:
            assert f"Analysis for {dim['title']}." in blocks[i], blocks[i]
            assert "Analysis unavailable" not in blocks[i]

    # The analysis must still complete and save content overall.
    assert analysis.content and "Analysis unavailable" in analysis.content
    complete_events = [e for e in events if e.startswith("event: complete")]
    assert len(complete_events) == 1, "complete event must still fire despite one failed dimension"


# ── (e) _build_dimension_prompt contains scale anchors + every criteria bullet ──

def test_build_dimension_prompt_contains_scale_and_criteria():
    for dim in RISK_DIMENSIONS:
        prompt = _build_dimension_prompt(dim)
        assert dim["title"] in prompt, (dim["key"], prompt)
        assert dim["scale"] in prompt, (dim["key"], prompt)
        for criterion in dim["criteria"]:
            assert criterion in prompt, (dim["key"], criterion, prompt)

        # And no OTHER dimension's title leaks into this one's prompt.
        for other in RISK_DIMENSIONS:
            if other is dim:
                continue
            assert other["title"] not in prompt, (
                f"{dim['key']} prompt unexpectedly mentions {other['title']!r}"
            )


# ── Test runner ───────────────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run_test(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")


if __name__ == "__main__":
    print("\nRunning risk_dimensions parallel-analysis tests...\n")

    _run_test("exactly 6 dimension calls, isolated titles", test_exactly_six_dimension_calls_with_isolated_titles)
    _run_test("six dimension calls run concurrently", test_six_dimension_calls_run_concurrently)
    _run_test("out-of-order completion preserves canonical emission order", test_out_of_order_completion_preserves_canonical_emission_order)
    _run_test("one dimension failure yields placeholder, others intact", test_one_dimension_failure_yields_placeholder_others_intact)
    _run_test("_build_dimension_prompt contains scale + criteria", test_build_dimension_prompt_contains_scale_and_criteria)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
