"""Tests for services/research_agent.py's pure prompt-builder functions, and
for the _record_source helper (T-17: dedupe of the "record a new source"
block that used to be copy-pasted between the initial per-source loop and
the gap-closing loop).

build_gap_check_prompt and build_synthesis_prompt are pure (no I/O, no
randomness), same as build_revision_prompt in services/report_quality.py —
so they're tested the same way tests/test_report_revision.py tests
build_revision_prompt: direct content assertions, no monkeypatching, no
network. (build_decomposition_prompt / build_source_summary_prompt, the
OTHER pure prompt builders in this module, are instead exercised live via
evals/eval_research_queries.py and evals/eval_being_specific.py — those
evals score actual model output and need an API key, which isn't
appropriate for the fast/offline suite these tests run in.)

_record_source is not pure (it touches a db session, a list, and an asyncio
queue) but needs no network, so it's tested directly with a fake db object
and a real asyncio.Queue, run via asyncio.run — nothing here was previously
covered: this file only tested the prompt builders before T-17.

Run from the backend directory:
    ./venv/bin/python -m tests.test_research_agent
"""
import asyncio
import json
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from models import SearchResult
from services.research_agent import build_gap_check_prompt, build_synthesis_prompt, _record_source
from services.tavily_client import SearchResult as TavilyResult

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


# ── _record_source (T-17) ───────────────────────────────────────────────────

class _FakeDB:
    """Records add()/commit() calls without a real SQLAlchemy session.

    _record_source's contract after T-17 is that it does NOT commit — callers
    commit once after their loop — so the fake tracks commit() calls
    separately from add() calls to make that assertable.
    """
    def __init__(self):
        self.added = []
        self.commit_calls = 0

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commit_calls += 1


def _tavily_result(url="https://a.example.com", title="Source A", content="full content"):
    return TavilyResult(
        url=url, title=title, snippet="a snippet", content=content,
        score=0.75, published_date="2026-01-01",
    )


def _parsed_event(raw: str) -> tuple[str, dict]:
    """Split a sse_event()-formatted string back into (event_name, data)."""
    header, data_line, *_ = raw.split("\n")
    return header.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))


def test_record_source_builds_search_result_row_with_all_fields():
    db = _FakeDB()
    db_results, summarized, queue = [], [], asyncio.Queue()
    result = _tavily_result()

    asyncio.run(_record_source(
        result, "AI summary text", "tier1", 0,
        "session-123", db, db_results, summarized, queue,
    ))

    assert len(db.added) == 1
    row = db.added[0]
    assert isinstance(row, SearchResult)
    assert row.id  # client-generated uuid4, non-empty before any commit
    assert row.session_id == "session-123"
    assert row.url == result.url
    assert row.title == result.title
    assert row.snippet == result.snippet
    assert row.full_content == result.content
    assert row.relevance_score == result.score
    assert row.ai_summary == "AI summary text"
    assert row.source_tier == "tier1"
    assert row.published_date == result.published_date
    assert row.result_order == 0


def test_record_source_truncates_full_content_to_10k_chars():
    db = _FakeDB()
    db_results, summarized, queue = [], [], asyncio.Queue()
    result = _tavily_result(content="x" * 20000)

    asyncio.run(_record_source(
        result, "summary", "tier1", 0, "s1", db, db_results, summarized, queue,
    ))

    assert len(db.added[0].full_content) == 10000


def test_record_source_does_not_commit():
    # T-17: commit moved out of the per-source helper to a single commit
    # after each caller's loop — the row's id is a client-generated uuid4
    # (asserted above), so nothing needs it durable before the next iteration.
    db = _FakeDB()
    db_results, summarized, queue = [], [], asyncio.Queue()
    result = _tavily_result()

    asyncio.run(_record_source(
        result, "summary", "tier1", 0, "s1", db, db_results, summarized, queue,
    ))

    assert db.commit_calls == 0


def test_record_source_appends_to_db_results_and_summarized_with_order_plus_one():
    db = _FakeDB()
    db_results, summarized, queue = [], [], asyncio.Queue()
    result = _tavily_result(url="https://b.example.com", title="Source B")

    asyncio.run(_record_source(
        result, "B summary", "tier2", 4, "s1", db, db_results, summarized, queue,
    ))

    assert db_results == [db.added[0]]
    assert summarized == [{
        "order": 5,
        "title": "Source B",
        "url": "https://b.example.com",
        "summary": "B summary",
        "score": result.score,
        "tier": "tier2",
    }]


def test_record_source_emits_source_processed_event_matching_inputs():
    db = _FakeDB()
    db_results, summarized, queue = [], [], asyncio.Queue()
    result = _tavily_result()

    asyncio.run(_record_source(
        result, "summary text", "tier1", 2, "s1", db, db_results, summarized, queue,
    ))

    assert queue.qsize() == 1
    name, data = _parsed_event(queue.get_nowait())
    assert name == "source_processed"
    assert data == {
        "order": 3,
        "title": result.title,
        "url": result.url,
        "snippet": result.snippet,
        "ai_summary": "summary text",
        "tier": "tier1",
    }


def test_record_source_numbers_contiguously_across_the_two_real_call_patterns():
    # Mirrors the two real call sites now unified behind _record_source:
    # Step 3 numbers with enumerate() from 0; the gap-closing loop continues
    # with order = len(summarized). After unifying both behind one helper,
    # calling it that way must still number sources 1..N contiguously with
    # no gap or reset between "rounds".
    db = _FakeDB()
    db_results, summarized, queue = [], [], asyncio.Queue()

    async def _run():
        # Step-3-style: enumerate() from 0.
        first_round = [_tavily_result(f"https://s{i}.example.com", f"S{i}") for i in range(2)]
        for order, result in enumerate(first_round):
            await _record_source(
                result, f"sum{order}", "tier1", order,
                "s1", db, db_results, summarized, queue,
            )

        # Gap-closing-style: order = len(summarized).
        gap_result = _tavily_result("https://s2.example.com", "S2")
        order = len(summarized)
        await _record_source(
            gap_result, "sum2", "tier1", order,
            "s1", db, db_results, summarized, queue,
        )

    asyncio.run(_run())

    assert [s["order"] for s in summarized] == [1, 2, 3]
    assert [r.result_order for r in db_results] == [0, 1, 2]
    assert queue.qsize() == 3


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
    _run("_record_source builds SearchResult row with all fields", test_record_source_builds_search_result_row_with_all_fields)
    _run("_record_source truncates full_content to 10k chars", test_record_source_truncates_full_content_to_10k_chars)
    _run("_record_source does not commit", test_record_source_does_not_commit)
    _run("_record_source appends to db_results and summarized with order+1", test_record_source_appends_to_db_results_and_summarized_with_order_plus_one)
    _run("_record_source emits source_processed event matching inputs", test_record_source_emits_source_processed_event_matching_inputs)
    _run("_record_source numbers contiguously across the two real call patterns", test_record_source_numbers_contiguously_across_the_two_real_call_patterns)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
