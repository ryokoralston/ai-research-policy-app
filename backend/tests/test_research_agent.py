"""Tests for services/research_agent.py's pure prompt-builder functions.

build_gap_check_prompt and build_synthesis_prompt are pure (no I/O, no
randomness), same as build_revision_prompt in services/report_quality.py —
so they're tested the same way tests/test_report_revision.py tests
build_revision_prompt: direct content assertions, no monkeypatching, no
network. (build_decomposition_prompt / build_source_summary_prompt, the
OTHER pure prompt builders in this module, are instead exercised live via
evals/eval_research_queries.py and evals/eval_being_specific.py — those
evals score actual model output and need an API key, which isn't
appropriate for the fast/offline suite these tests run in.)

Run from the backend directory:
    ./venv/bin/python -m tests.test_research_agent
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from services.research_agent import build_gap_check_prompt, build_synthesis_prompt

_QUERY = "What are the main AI governance risks from autonomous weapons systems?"
_SYNTHESIS = (
    "## Key Findings\n- Some finding [Source 1]\n\n"
    "## Evidence Gaps\n"
    "- The synthesis does not quantify how many states have ratified relevant "
    "CCW protocols.\n"
    "- No source addresses classified military testing programs.\n"
)


# ── build_gap_check_prompt ────────────────────────────────────────────────────

def test_gap_check_prompt_contains_query_and_synthesis():
    prompt = build_gap_check_prompt(_QUERY, _SYNTHESIS)
    assert _QUERY in prompt
    assert _SYNTHESIS in prompt
    assert "<synthesis>" in prompt and "</synthesis>" in prompt


def test_gap_check_prompt_has_json_array_instruction_and_no_fence():
    prompt = build_gap_check_prompt(_QUERY, _SYNTHESIS)
    assert "Return ONLY a JSON array" in prompt
    # generate_json's caller supplies the '```json' prefill — the prompt text
    # itself must not include a fence, or the model would double it up.
    assert "```" not in prompt


def test_gap_check_prompt_instructs_empty_array_and_worked_example():
    prompt = build_gap_check_prompt(_QUERY, _SYNTHESIS)
    assert "empty array" in prompt.lower()
    assert "<example>" in prompt and "</example>" in prompt
    assert "<sample_input>" in prompt and "<ideal_output>" in prompt
    # The worked example must not reuse the exact test synthesis text — it's
    # a separate illustrative case, same discipline as build_decomposition_prompt.
    assert "autonomous weapons" not in prompt.split("</example>")[0].split("<example>")[1]


def test_gap_check_prompt_mentions_proprietary_data_limitation():
    prompt = build_gap_check_prompt(_QUERY, _SYNTHESIS)
    assert "proprietary" in prompt.lower()


# ── build_synthesis_prompt ────────────────────────────────────────────────────

_SUMMARIZED = [
    {"order": 1, "title": "Source A", "url": "https://a.example.com", "summary": "Summary A.", "score": 0.9},
    {"order": 2, "title": "Source B", "url": "https://b.example.com", "summary": "Summary B.", "score": 0.8},
]


def test_synthesis_prompt_contains_query_and_all_sources():
    prompt = build_synthesis_prompt(_QUERY, _SUMMARIZED)
    assert _QUERY in prompt
    for s in _SUMMARIZED:
        assert s["title"] in prompt
        assert s["url"] in prompt
        assert s["summary"] in prompt
    assert "You have analyzed 2 sources" in prompt


def test_synthesis_prompt_has_all_required_sections():
    prompt = build_synthesis_prompt(_QUERY, _SUMMARIZED)
    for heading in (
        "## Key Findings",
        "## Areas of Consensus",
        "## Areas of Uncertainty or Debate",
        "## Evidence Gaps",
        "## Recommended Further Research",
    ):
        assert heading in prompt, f"missing section: {heading}"


def test_synthesis_prompt_wraps_sources_in_xml_tag():
    prompt = build_synthesis_prompt(_QUERY, _SUMMARIZED)
    assert "<source_summaries>" in prompt and "</source_summaries>" in prompt


def test_synthesis_prompt_is_stable_across_calls_with_same_input():
    # Pure function: no I/O, no randomness — same input, same output.
    assert build_synthesis_prompt(_QUERY, _SUMMARIZED) == build_synthesis_prompt(_QUERY, _SUMMARIZED)


def test_synthesis_prompt_reflects_growing_cumulative_source_list():
    # This is what the gap-closing loop relies on: calling build_synthesis_prompt
    # again with a longer `summarized` list (original + gap-round sources)
    # must produce a prompt that reflects the new total and includes the new
    # source, not just the original two.
    grown = _SUMMARIZED + [
        {"order": 3, "title": "Gap Source C", "url": "https://c.example.com", "summary": "Summary C.", "score": 0.7}
    ]
    prompt = build_synthesis_prompt(_QUERY, grown)
    assert "You have analyzed 3 sources" in prompt
    assert "Gap Source C" in prompt
    assert "https://c.example.com" in prompt


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
    print("\nRunning research_agent prompt-builder tests...\n")

    _run("gap check prompt contains query and synthesis", test_gap_check_prompt_contains_query_and_synthesis)
    _run("gap check prompt has JSON array instruction and no fence", test_gap_check_prompt_has_json_array_instruction_and_no_fence)
    _run("gap check prompt instructs empty array + worked example", test_gap_check_prompt_instructs_empty_array_and_worked_example)
    _run("gap check prompt mentions proprietary data limitation", test_gap_check_prompt_mentions_proprietary_data_limitation)
    _run("synthesis prompt contains query and all sources", test_synthesis_prompt_contains_query_and_all_sources)
    _run("synthesis prompt has all required sections", test_synthesis_prompt_has_all_required_sections)
    _run("synthesis prompt wraps sources in XML tag", test_synthesis_prompt_wraps_sources_in_xml_tag)
    _run("synthesis prompt is stable across calls with same input", test_synthesis_prompt_is_stable_across_calls_with_same_input)
    _run("synthesis prompt reflects growing cumulative source list", test_synthesis_prompt_reflects_growing_cumulative_source_list)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
