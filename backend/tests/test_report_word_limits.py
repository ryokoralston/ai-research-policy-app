"""Tests for word-limit extraction and per-section budgets (report_generator).

Run from the backend directory:
    ./venv/bin/python -m tests.test_report_word_limits
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from services.report_generator import (
    _extract_word_limit,
    _calculate_word_budgets,
    _build_shared_context,
    _build_section_prompt,
    _build_single_pass_shared_context,
)


# ── _extract_word_limit ───────────────────────────────────────────────────────

def test_english_patterns():
    assert _extract_word_limit("200 words or less") == 200
    assert _extract_word_limit("keep it under 300 words") == 300
    assert _extract_word_limit("max 150 words please") == 150
    assert _extract_word_limit("at most 500 words") == 500
    assert _extract_word_limit("1000 words total") == 1000


def test_japanese_patterns():
    assert _extract_word_limit("300語以内でお願いします") == 300
    assert _extract_word_limit("500ワード以下") == 500
    assert _extract_word_limit("400 words 以内") == 400


def test_no_limit_returns_none():
    assert _extract_word_limit(None) is None
    assert _extract_word_limit("") is None
    assert _extract_word_limit("focus on the EU AI Act") is None
    assert _extract_word_limit("use simple words") is None


# ── _calculate_word_budgets ───────────────────────────────────────────────────

_SECTIONS = [
    {"key": "a", "title": "A", "instructions": "Write 100-200 words on background."},
    {"key": "b", "title": "B", "instructions": "About 300 words of analysis."},
    {"key": "c", "title": "C", "instructions": "No explicit count here."},  # default 80
]


def test_budgets_none_without_limit():
    assert _calculate_word_budgets(_SECTIONS, None) is None
    assert _calculate_word_budgets(_SECTIONS, "no limit mentioned") is None


def test_budgets_are_proportional():
    budgets = _calculate_word_budgets(_SECTIONS, "265 words or less")
    # defaults: (100+200)//2=150, 300, 80 → total 530; limit 265 = half
    assert budgets == [75, 150, 40], budgets


def test_budgets_have_floor():
    budgets = _calculate_word_budgets(_SECTIONS, "30 words max")
    assert all(b >= 15 for b in budgets), budgets


# ── _build_shared_context / _build_section_prompt (prompt-caching split) ───────
# Sanity checks for the cached_context split: the large source material must
# live in the shared/cached part, never in the per-section (varying) part,
# and the shared part must be byte-identical across different section calls
# that share the same report-level inputs (title/type/audience/material).

_SOURCE_MATERIAL = "The EU AI Act establishes a risk-based regulatory framework " * 20


def test_shared_context_contains_source_material():
    shared = _build_shared_context(
        report_title="EU AI Policy Brief",
        report_type="policy_brief",
        audience="Congressional staff",
        source_material=_SOURCE_MATERIAL,
    )
    assert _SOURCE_MATERIAL[:50] in shared, shared[:200]
    assert "EU AI Policy Brief" in shared
    assert "Congressional staff" in shared


def test_section_prompt_does_not_contain_source_material():
    prompt = _build_section_prompt(
        section_def={"key": "background", "title": "Background", "instructions": "Summarize context."},
        custom_instructions=None,
        previous_sections=[],
        word_budget=None,
    )
    assert _SOURCE_MATERIAL[:50] not in prompt, prompt
    assert "Background" in prompt
    assert "Summarize context." in prompt


def test_shared_context_byte_identical_across_different_section_calls():
    # Same report-level inputs, called twice (simulating two different
    # sections in the same generate_report_stream loop) — must be byte-for-byte
    # identical so the cache_control breakpoint actually hits.
    kwargs = dict(
        report_title="EU AI Policy Brief",
        report_type="policy_brief",
        audience="Congressional staff",
        source_material=_SOURCE_MATERIAL,
    )
    first_call = _build_shared_context(**kwargs)
    second_call = _build_shared_context(**kwargs)
    assert first_call == second_call, (first_call, second_call)


def test_section_prompt_varies_with_previous_sections_and_word_budget():
    prompt_no_history = _build_section_prompt(
        section_def={"key": "risks", "title": "Risks", "instructions": "List risks."},
        custom_instructions=None,
        previous_sections=[],
        word_budget=None,
    )
    prompt_with_history = _build_section_prompt(
        section_def={"key": "risks", "title": "Risks", "instructions": "List risks."},
        custom_instructions=None,
        previous_sections=[{"title": "Background", "content": "Some background content."}],
        word_budget=150,
    )
    assert prompt_no_history != prompt_with_history
    assert "Background" in prompt_with_history
    assert "150 words" in prompt_with_history


def test_single_pass_shared_context_contains_source_material():
    shared = _build_single_pass_shared_context(_SOURCE_MATERIAL)
    assert _SOURCE_MATERIAL[:50] in shared, shared[:200]


def test_concatenated_shared_and_section_prompt_matches_pre_split_text():
    """Concatenating the shared context with the per-section prompt must
    reproduce exactly the single prompt string this module built before the
    cached_context split (same text, just partitioned — see anthropic_client
    prompt-caching deliverable)."""
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
    print("\nRunning word-limit tests...\n")

    _run("English word-limit patterns", test_english_patterns)
    _run("Japanese word-limit patterns", test_japanese_patterns)
    _run("no limit returns None", test_no_limit_returns_none)
    _run("budgets None without limit", test_budgets_none_without_limit)
    _run("budgets are proportional", test_budgets_are_proportional)
    _run("budgets have a floor", test_budgets_have_floor)

    _run("shared context contains source material", test_shared_context_contains_source_material)
    _run("section prompt excludes source material", test_section_prompt_does_not_contain_source_material)
    _run("shared context byte-identical across section calls", test_shared_context_byte_identical_across_different_section_calls)
    _run("section prompt varies with history/word budget", test_section_prompt_varies_with_previous_sections_and_word_budget)
    _run("single-pass shared context contains source material", test_single_pass_shared_context_contains_source_material)
    _run("concatenated shared+section prompt matches pre-split text", test_concatenated_shared_and_section_prompt_matches_pre_split_text)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
