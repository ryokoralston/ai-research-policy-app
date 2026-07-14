"""Tests for services/query_router.py (the routing-workflow classifier) and
its wiring into services/rag_service.answer_question.

No live Claude API calls — generate_text and stream_chat_with_tools are
faked throughout. Run from the backend directory:
    ./venv/bin/python -m tests.test_query_router
"""
import asyncio
import os
import sys
import types

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

# rag_service imports chromadb / sentence_transformers transitively (via
# rag.vector_store / services.embedding_service) — stub them out so this
# module never needs those packages installed, same as test_rag_answer.py.
for _name in ("chromadb", "sentence_transformers"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import services.query_router as query_router
from services.query_router import (
    QUERY_CATEGORIES,
    build_router_prompt,
    history_snippet,
    parse_category,
    route_query,
    guidance_for,
)
import rag.retriever as retriever_module
import services.rag_service as rag_service


# ── parse_category ───────────────────────────────────────────────────────────

def test_parse_category_exact_key():
    assert parse_category("comparison") == "comparison"


def test_parse_category_uppercase_and_whitespace():
    assert parse_category("  COMPARISON  \n") == "comparison"


def test_parse_category_wrapped_in_quotes_backticks_period():
    assert parse_category("`comparison`.") == "comparison"
    assert parse_category('"comparison"') == "comparison"
    assert parse_category("comparison.") == "comparison"


def test_parse_category_garbage_returns_general():
    assert parse_category("xyz not a category") == "general"
    assert parse_category("") == "general"


def test_parse_category_single_key_substring_matches():
    assert parse_category("The category is comparison.") == "comparison"


def test_parse_category_ambiguous_two_keys_returns_general():
    # Both "factual_lookup" and "general" appear as substrings — ambiguous.
    text = "not sure if this is factual_lookup or general"
    assert parse_category(text) == "general"


# ── build_router_prompt ──────────────────────────────────────────────────────

def test_build_router_prompt_contains_question_and_all_keys():
    prompt = build_router_prompt("What year was the EU AI Act passed?")
    assert "What year was the EU AI Act passed?" in prompt
    for key in QUERY_CATEGORIES:
        assert key in prompt, key


def test_build_router_prompt_omits_recent_conversation_when_empty():
    prompt = build_router_prompt("A question", "")
    assert "<recent_conversation>" not in prompt


def test_build_router_prompt_includes_recent_conversation_when_present():
    prompt = build_router_prompt("A question", "User: earlier question\nAssistant: earlier answer")
    assert "<recent_conversation>" in prompt
    assert "earlier question" in prompt
    assert "earlier answer" in prompt


# ── history_snippet ───────────────────────────────────────────────────────────

def test_history_snippet_none_returns_empty():
    assert history_snippet(None) == ""
    assert history_snippet([]) == ""


def test_history_snippet_str_contents():
    chat_history = [
        {"role": "user", "content": "What is the EU AI Act?"},
        {"role": "assistant", "content": "It is an EU regulation."},
    ]
    snippet = history_snippet(chat_history)
    assert "What is the EU AI Act?" in snippet
    assert "It is an EU regulation." in snippet


def test_history_snippet_skips_tool_blocks():
    chat_history = [
        {"role": "user", "content": "Search for something"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "search_documents", "input": {"query": "x"}},
                {"type": "text", "text": "Here is what I found."},
            ],
        },
    ]
    snippet = history_snippet(chat_history)
    assert "Here is what I found." in snippet
    assert "search_documents" not in snippet
    assert "tool_use" not in snippet


def test_history_snippet_truncation_keeps_end():
    long_text = "".join(f"sentence{i} " for i in range(200))  # well over 500 chars
    chat_history = [{"role": "user", "content": long_text}]
    snippet = history_snippet(chat_history, max_chars=50)
    assert len(snippet) <= 50
    # The tail of the snippet must match the tail of the original — truncation
    # drops the front, keeps the most recent (end) text.
    assert long_text.strip().endswith(snippet.strip()[-20:])


# ── route_query ───────────────────────────────────────────────────────────────

def test_route_query_happy_path():
    async def fake_generate_text(prompt, **kwargs):
        return " Comparison\n"

    orig = query_router.generate_text
    query_router.generate_text = fake_generate_text
    try:
        result = asyncio.run(route_query("Compare the EU and US approaches"))
    finally:
        query_router.generate_text = orig
    assert result == "comparison"


def test_route_query_exception_falls_back_to_general():
    async def raising_generate_text(prompt, **kwargs):
        raise RuntimeError("boom")

    orig = query_router.generate_text
    query_router.generate_text = raising_generate_text
    try:
        result = asyncio.run(route_query("Any question"))
    finally:
        query_router.generate_text = orig
    assert result == "general"


# ── rag_service wiring ───────────────────────────────────────────────────────

def _run_answer_question_with_fakes(fixed_category: str):
    """Drive answer_question with route_query and stream_chat_with_tools both
    faked. Returns (events, captured) where captured["system"] is the system
    kwarg stream_chat_with_tools was called with.
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    captured: dict = {}

    async def fake_route_query(question, chat_history=None):
        return fixed_category

    async def fake_stream_chat_with_tools(messages, system="", tools=None, tool_executor=None, **kwargs):
        captured["system"] = system
        # Empty async generator: no tool calls, no text — the wiring test
        # only cares about the system prompt and the "route" SSE event.
        if False:
            yield

    class FakeRetriever:
        def retrieve(self, query, top_k=5, doc_ids=None):
            return []

    orig_route_query = rag_service.route_query
    orig_stream = rag_service.stream_chat_with_tools
    orig_retriever = retriever_module.Retriever
    rag_service.route_query = fake_route_query
    rag_service.stream_chat_with_tools = fake_stream_chat_with_tools
    retriever_module.Retriever = FakeRetriever
    try:
        async def collect():
            return [e async for e in rag_service.answer_question("q?", None, 5, db)]
        events = asyncio.run(collect())
    finally:
        rag_service.route_query = orig_route_query
        rag_service.stream_chat_with_tools = orig_stream
        retriever_module.Retriever = orig_retriever
        db.close()

    return events, captured


def test_wiring_comparison_category_appends_response_style():
    events, captured = _run_answer_question_with_fakes("comparison")
    expected_guidance = guidance_for("comparison")
    assert captured["system"].endswith(
        f"\n\n<response_style>\n{expected_guidance}\n</response_style>"
    ), captured["system"][-300:]


def test_wiring_general_category_no_response_style():
    events, captured = _run_answer_question_with_fakes("general")
    assert "<response_style>" not in captured["system"]


def test_wiring_emits_route_event():
    events, _ = _run_answer_question_with_fakes("comparison")
    route_events = [e for e in events if e.startswith("event: route")]
    assert len(route_events) == 1, events
    assert '"category": "comparison"' in route_events[0]


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
    print("\nRunning query router tests...\n")

    _run("parse_category: exact key", test_parse_category_exact_key)
    _run("parse_category: uppercase/whitespace", test_parse_category_uppercase_and_whitespace)
    _run("parse_category: wrapped in quotes/backticks/period", test_parse_category_wrapped_in_quotes_backticks_period)
    _run("parse_category: garbage -> general", test_parse_category_garbage_returns_general)
    _run("parse_category: single key substring matches", test_parse_category_single_key_substring_matches)
    _run("parse_category: ambiguous two keys -> general", test_parse_category_ambiguous_two_keys_returns_general)

    _run("build_router_prompt: contains question and all keys", test_build_router_prompt_contains_question_and_all_keys)
    _run("build_router_prompt: omits recent_conversation when empty", test_build_router_prompt_omits_recent_conversation_when_empty)
    _run("build_router_prompt: includes recent_conversation when present", test_build_router_prompt_includes_recent_conversation_when_present)

    _run("history_snippet: None/empty -> ''", test_history_snippet_none_returns_empty)
    _run("history_snippet: str contents", test_history_snippet_str_contents)
    _run("history_snippet: skips tool blocks", test_history_snippet_skips_tool_blocks)
    _run("history_snippet: truncation keeps end", test_history_snippet_truncation_keeps_end)

    _run("route_query: happy path", test_route_query_happy_path)
    _run("route_query: exception -> general", test_route_query_exception_falls_back_to_general)

    _run("wiring: comparison category appends response_style", test_wiring_comparison_category_appends_response_style)
    _run("wiring: general category has no response_style", test_wiring_general_category_no_response_style)
    _run("wiring: emits route event", test_wiring_emits_route_event)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
