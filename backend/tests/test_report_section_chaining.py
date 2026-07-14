"""Tests for the summary-section chaining fix in report_generator.py.

A "summarize_body" section (congressional_brief's executive_summary,
policy_memo's bluf) must be GENERATED LAST — after every body section already
exists — so it can faithfully distill the actual report instead of being
drafted from raw source material before the report it claims to summarize
exists. Display/document order (canonical template order, summary first)
must be restored regardless of generation order, and previous_sections
continuity for body sections must never include a summary section.

stream_text_with_thinking / verify_grounding are monkeypatched — no API calls.

Run from the backend directory:
    ./venv/bin/python -m tests.test_report_section_chaining
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
from models import Report, ReportSection, ResearchSession
from schemas import ReportGenerateRequest
import services.report_generator as report_generator
import services.report_quality as report_quality
from services.report_generator import _build_shared_context, _build_section_prompt

_NO_CLAIMS_GRADE = {"confidence_score": 9, "unsupported_claims": [], "notes": "well grounded"}

# Long enough (>500 chars) that if a summary prompt were built from a
# 500-char-truncated preview (the previous_sections behavior _build_section_prompt
# uses), this tail marker would be cut off and absent from the prompt.
_LONG_TAIL_MARKER = "BG_HEAD_MARKER " + ("x" * 600) + " BG_TAIL_UNIQUE_END"


# ── Fixtures / fakes ──────────────────────────────────────────────────────────

def _make_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_report_with_session(db, report_type):
    session = ResearchSession(
        id=str(uuid.uuid4()), query="test query", status="complete",
        summary="AI systems pose several governance risks that Congress should address.",
    )
    db.add(session)
    report_id = str(uuid.uuid4())
    db.add(Report(id=report_id, title="Test Report", report_type=report_type,
                   status="draft", session_id=session.id))
    db.commit()
    return report_id, session.id


def _sse_events(events, event_name):
    """Parse (event_name, data_dict) pairs out of raw SSE strings for a given event name."""
    out = []
    for e in events:
        lines = e.split("\n")
        name = lines[0][len("event: "):]
        if name != event_name:
            continue
        data = json.loads(lines[1][len("data: "):])
        out.append(data)
    return out


def _make_fake_stream(section_contents, calls, prompts):
    """section_contents: {title: content}. Records generation order (by
    title, matched via the "Now write the '<title>' section" substring both
    _build_section_prompt and _build_summary_section_prompt emit) into
    `calls`, and (title, prompt) pairs into `prompts` for prompt-content
    assertions."""
    async def fake_stream(prompt, system="", model=None, max_tokens=8192, cached_context=None, usage_log_tag=None):
        title = next(
            (t for t in section_contents if f"Now write the '{t}' section" in prompt), None
        )
        assert title is not None, f"could not match prompt to a known section title:\n{prompt[:300]}"
        calls.append(title)
        prompts.append((title, prompt))
        yield ("thinking", "thinking...")
        yield ("text", section_contents[title])
    return fake_stream


async def _no_claims_verify(content, source_material):
    return dict(_NO_CLAIMS_GRADE)


def _patch(fake_stream, fake_verify=None):
    fake_verify = fake_verify or _no_claims_verify
    orig = (
        report_generator.stream_text_with_thinking, report_generator.verify_grounding,
        report_quality.stream_text_with_thinking, report_quality.verify_grounding,
    )
    report_generator.stream_text_with_thinking = fake_stream
    report_generator.verify_grounding = fake_verify
    report_quality.stream_text_with_thinking = fake_stream
    report_quality.verify_grounding = fake_verify
    return orig


def _unpatch(orig):
    (report_generator.stream_text_with_thinking, report_generator.verify_grounding,
     report_quality.stream_text_with_thinking, report_quality.verify_grounding) = orig


def _run_generation(db, report_id, request, fake_stream):
    orig = _patch(fake_stream)
    try:
        async def collect():
            return [e async for e in report_generator.generate_report_stream(report_id, request, db)]
        events = asyncio.run(collect())
    finally:
        _unpatch(orig)
    return events


def _run_report_generation(report_type):
    """Generate a fake report end-to-end for `report_type` and return
    everything needed to assert on it: call order, per-call prompts, the
    saved Report/ReportSection rows, and the SSE event stream."""
    template_sections = report_generator.TEMPLATES[report_type]["sections"]
    titles = [s["title"] for s in template_sections]

    section_contents_by_key = {}
    for s in template_sections:
        content = f"CONTENT_MARKER_{s['key']}"
        if s["key"] == "background":
            content += " " + _LONG_TAIL_MARKER
        section_contents_by_key[s["key"]] = content
    section_contents = {s["title"]: section_contents_by_key[s["key"]] for s in template_sections}

    calls, prompts = [], []
    fake_stream = _make_fake_stream(section_contents, calls, prompts)

    db = _make_db()
    report_id, session_id = _make_report_with_session(db, report_type)
    request = ReportGenerateRequest(report_type=report_type, title="Test Report", session_id=session_id)

    events = _run_generation(db, report_id, request, fake_stream)

    report = db.query(Report).filter(Report.id == report_id).first()
    db.refresh(report)
    sections = (
        db.query(ReportSection)
        .filter(ReportSection.report_id == report_id)
        .order_by(ReportSection.order_index)
        .all()
    )

    return {
        "db": db, "report": report, "events": events, "calls": calls,
        "prompts": prompts, "section_contents": section_contents, "titles": titles,
        "sections": sections,
    }


# ── (a) congressional_brief: executive_summary generated LAST ────────────────

def test_congressional_brief_executive_summary_generated_last():
    r = _run_report_generation("congressional_brief")
    titles = r["titles"]
    calls = r["calls"]
    expected_body_order = [t for t in titles if t != "Executive Summary"]

    assert calls[-1] == "Executive Summary", calls
    assert calls.count("Executive Summary") == 1, calls
    assert calls[:-1] == expected_body_order, calls
    r["db"].close()


def test_congressional_brief_sse_section_start_matches_generation_order():
    r = _run_report_generation("congressional_brief")
    expected_body_order = [t for t in r["titles"] if t != "Executive Summary"]
    starts = _sse_events(r["events"], "section_start")
    assert [d["title"] for d in starts] == expected_body_order + ["Executive Summary"]
    r["db"].close()


# ── (b) full_content restored to canonical order (summary displayed first) ───

def test_congressional_brief_full_content_canonical_order():
    r = _run_report_generation("congressional_brief")
    content = r["report"].content
    assert "## Executive Summary" in content and "## Background & Context" in content
    assert content.index("## Executive Summary") < content.index("## Background & Context"), content[:300]
    r["db"].close()


# ── (c) summary prompt contains the FULL body (not 500-char truncation) ──────

def test_congressional_brief_summary_prompt_has_full_body_and_no_new_claims_instruction():
    r = _run_report_generation("congressional_brief")
    background_content = r["section_contents"]["Background & Context"]

    assert "BG_TAIL_UNIQUE_END" in background_content
    # Sanity check the fixture actually exercises the truncation risk:
    assert "BG_TAIL_UNIQUE_END" not in background_content[:500]

    summary_prompt = next(p for t, p in r["prompts"] if t == "Executive Summary")
    assert "BG_TAIL_UNIQUE_END" in summary_prompt, (
        "executive_summary prompt must contain the FULL body, not a 500-char truncation"
    )
    for t in r["titles"]:
        if t == "Executive Summary":
            continue
        assert r["section_contents"][t] in summary_prompt, f"missing full content for section '{t}'"

    assert "Do not introduce any claim, statistic, or recommendation" in summary_prompt
    r["db"].close()


# ── (d) executive_summary never leaks into any body section's previous_sections ──

def test_congressional_brief_executive_summary_never_in_body_previous_sections():
    r = _run_report_generation("congressional_brief")
    exec_summary_content = r["section_contents"]["Executive Summary"]
    body_prompts = [p for t, p in r["prompts"] if t != "Executive Summary"]

    assert body_prompts, "expected at least one body section prompt to have been recorded"
    for p in body_prompts:
        assert "### Executive Summary" not in p, "a body prompt's previous_sections must never include the summary"
        assert exec_summary_content not in p
    r["db"].close()


# ── (e) ReportSection.order_index values are canonical template indices ──────

def test_congressional_brief_order_index_is_canonical():
    r = _run_report_generation("congressional_brief")
    sections = r["sections"]
    assert [s.order_index for s in sections] == list(range(len(r["titles"])))
    assert [s.title for s in sections] == r["titles"]
    r["db"].close()


# ── (f) policy_memo: bluf deferred likewise ───────────────────────────────────

def test_policy_memo_bluf_generated_last_and_canonical_order_restored():
    r = _run_report_generation("policy_memo")
    titles = r["titles"]
    bluf_title = "BOTTOM LINE UP FRONT"
    expected_body_order = [t for t in titles if t != bluf_title]

    assert r["calls"][-1] == bluf_title, r["calls"]
    assert r["calls"][:-1] == expected_body_order, r["calls"]

    content = r["report"].content
    assert content.index("## SUBJECT") < content.index(f"## {bluf_title}") < content.index("## Background"), content[:300]

    sections = r["sections"]
    assert [s.order_index for s in sections] == list(range(len(titles)))
    assert [s.title for s in sections] == titles
    r["db"].close()


# ── (g) template with no summarize_body flags → generation order unchanged ───

def test_synthetic_template_without_summarize_body_preserves_original_order():
    synthetic_sections = [
        {"key": "intro", "title": "Intro", "instructions": "Write an intro."},
        {"key": "middle", "title": "Middle", "instructions": "Write the middle."},
        {"key": "end", "title": "End", "instructions": "Write the end."},
    ]
    report_generator.TEMPLATES["synthetic_no_summary"] = {
        "system": "You are a test report writer.",
        "sections": synthetic_sections,
    }
    try:
        titles = [s["title"] for s in synthetic_sections]
        section_contents = {t: f"CONTENT_FOR_{t}" for t in titles}
        calls, prompts = [], []
        fake_stream = _make_fake_stream(section_contents, calls, prompts)

        db = _make_db()
        report_id, session_id = _make_report_with_session(db, "synthetic_no_summary")
        request = ReportGenerateRequest(report_type="synthetic_no_summary", title="Test Report", session_id=session_id)
        events = _run_generation(db, report_id, request, fake_stream)

        assert calls == titles, calls

        report = db.query(Report).filter(Report.id == report_id).first()
        db.refresh(report)
        assert (
            report.content.index("## Intro")
            < report.content.index("## Middle")
            < report.content.index("## End")
        ), report.content

        sections = (
            db.query(ReportSection)
            .filter(ReportSection.report_id == report_id)
            .order_by(ReportSection.order_index)
            .all()
        )
        assert [s.order_index for s in sections] == [0, 1, 2]
        db.close()
    finally:
        del report_generator.TEMPLATES["synthetic_no_summary"]


# ── (h) _build_section_prompt output byte-identical regression guard ─────────

def test_build_section_prompt_output_unchanged_by_chaining_refactor():
    """_build_section_prompt's output bytes must be exactly what they were
    before this refactor factored _word_budget_note / _custom_constraints_note
    out as shared helpers. Mirrors
    test_report_word_limits.test_concatenated_shared_and_section_prompt_matches_pre_split_text."""
    section_def = {"key": "risks", "title": "Risks", "instructions": "List risks."}
    shared = _build_shared_context(
        report_title="Title", report_type="policy_brief", audience="Staff",
        source_material="short material",
    )
    per_section = _build_section_prompt(
        section_def=section_def, custom_instructions="Focus on the EU.",
        previous_sections=[], word_budget=80,
    )
    combined = shared + per_section

    expected = (
        "You are writing a 'Policy Brief' report.\n"
        "Report title: Title\n"
        "Target audience: Staff\n\n"
        "<source_material>\nshort material\n</source_material>\n"
        "\n\n"
        "Now write the 'Risks' section.\n"
        "Instructions: List risks.\n\n"
        "Write ONLY the section content in Markdown (no section header — it will be added automatically). "
        "Do not repeat information already covered in previous sections."
        "\n\n⚠️ WORD LIMIT: Write this section in 80 words or fewer. "
        "This overrides any word count in the instructions above."
        "\n\n⚠️ MANDATORY USER CONSTRAINTS (override all other instructions): Focus on the EU."
    )
    assert combined == expected, combined


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
    print("\nRunning report section chaining tests...\n")

    _run("congressional_brief: executive_summary generated last", test_congressional_brief_executive_summary_generated_last)
    _run("congressional_brief: SSE section_start matches generation order", test_congressional_brief_sse_section_start_matches_generation_order)
    _run("congressional_brief: full_content restored to canonical order", test_congressional_brief_full_content_canonical_order)
    _run("congressional_brief: summary prompt has full body + no-new-claims instruction", test_congressional_brief_summary_prompt_has_full_body_and_no_new_claims_instruction)
    _run("congressional_brief: executive_summary never in body previous_sections", test_congressional_brief_executive_summary_never_in_body_previous_sections)
    _run("congressional_brief: order_index is canonical", test_congressional_brief_order_index_is_canonical)
    _run("policy_memo: bluf generated last, canonical order restored", test_policy_memo_bluf_generated_last_and_canonical_order_restored)
    _run("synthetic template without summarize_body preserves original order", test_synthetic_template_without_summarize_body_preserves_original_order)
    _run("_build_section_prompt output unchanged by chaining refactor", test_build_section_prompt_output_unchanged_by_chaining_refactor)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
