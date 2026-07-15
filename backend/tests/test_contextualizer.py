"""Tests for rag/contextualizer.py (Contextual Retrieval) and the
generate_text cached_prefix parameter it relies on.

generate_text/_generate_text_user_content are monkeypatched or exercised
against a fake Anthropic client — no live API calls. Mirrors
test_risk_dimensions_parallel.py's approach for the concurrency/ordering
assertions (in-flight counters, asyncio.Event-based staggering).

Run from the backend directory:
    ./venv/bin/python -m tests.test_contextualizer
"""
import asyncio
import contextlib
import io
import os
import sys
import types

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

import rag.contextualizer as contextualizer
import services.anthropic_client as anthropic_client
from rag.contextualizer import (
    MAX_DOC_CHARS,
    build_document_prefix,
    build_context_prompt,
    combine,
    contextualize_chunks,
)


# ── build_document_prefix ─────────────────────────────────────────────────────

def test_build_document_prefix_wraps_short_doc_untruncated():
    text = "This is the whole document."
    prefix = build_document_prefix(text)
    assert "<document>" in prefix and "</document>" in prefix
    assert text in prefix
    assert "truncated" not in prefix


def test_build_document_prefix_truncates_long_doc_with_marker():
    text = "x" * (MAX_DOC_CHARS + 500)
    prefix = build_document_prefix(text)
    assert "[... document truncated for context generation]" in prefix
    # The truncated body is exactly MAX_DOC_CHARS 'x's, not the full text.
    assert "x" * (MAX_DOC_CHARS + 500) not in prefix
    assert "x" * MAX_DOC_CHARS in prefix


def test_build_document_prefix_exactly_at_cap_untruncated():
    text = "y" * MAX_DOC_CHARS
    prefix = build_document_prefix(text)
    assert "truncated" not in prefix
    assert text in prefix


# ── build_context_prompt ──────────────────────────────────────────────────────

def test_build_context_prompt_contains_chunk_and_instruction():
    prompt = build_context_prompt("some chunk content here")
    assert "<chunk>" in prompt and "</chunk>" in prompt
    assert "some chunk content here" in prompt
    assert "succinct context" in prompt.lower()
    assert "answer only with the succinct context" in prompt.lower()


# ── combine ────────────────────────────────────────────────────────────────────

def test_combine_with_context_prepends_and_blank_line_separates():
    assert combine("situating context", "chunk body") == "situating context\n\nchunk body"


def test_combine_without_context_returns_content_unchanged():
    assert combine("", "chunk body") == "chunk body"


# ── contextualize_chunks: flag gate ─────────────────────────────────────────────

class _FakeSettings:
    def __init__(self, enabled):
        self.contextual_retrieval_enabled = enabled


def test_flag_off_returns_all_empty_without_calling_generate_text():
    orig_settings = contextualizer.get_settings
    orig_generate = contextualizer.generate_text
    contextualizer.get_settings = lambda: _FakeSettings(False)

    async def _must_not_be_called(*a, **k):
        raise AssertionError("generate_text must not be called when the flag is off")
    contextualizer.generate_text = _must_not_be_called
    try:
        result = asyncio.run(contextualize_chunks("full doc text", ["chunk a", "chunk b", "chunk c"]))
    finally:
        contextualizer.get_settings = orig_settings
        contextualizer.generate_text = orig_generate

    assert result == ["", "", ""], result


def test_empty_chunk_texts_returns_empty_list_without_calling_generate_text():
    orig_settings = contextualizer.get_settings
    orig_generate = contextualizer.generate_text
    contextualizer.get_settings = lambda: _FakeSettings(True)

    async def _must_not_be_called(*a, **k):
        raise AssertionError("generate_text must not be called for empty chunk_texts")
    contextualizer.generate_text = _must_not_be_called
    try:
        result = asyncio.run(contextualize_chunks("full doc text", []))
    finally:
        contextualizer.get_settings = orig_settings
        contextualizer.generate_text = orig_generate

    assert result == [], result


# ── contextualize_chunks: happy path + whitespace stripping ────────────────────

def test_contexts_aligned_to_chunk_order_and_stripped():
    orig_settings = contextualizer.get_settings
    orig_generate = contextualizer.generate_text
    contextualizer.get_settings = lambda: _FakeSettings(True)

    async def fake(prompt, cached_prefix=None, temperature=0.0, max_tokens=150):
        for i, text in enumerate(["alpha-chunk", "beta-chunk", "gamma-chunk"]):
            if text in prompt:
                return f"  context for {text}  \n"  # whitespace to be stripped
        raise AssertionError(f"unrecognized prompt: {prompt!r}")
    contextualizer.generate_text = fake
    try:
        result = asyncio.run(contextualize_chunks(
            "full doc text", ["alpha-chunk", "beta-chunk", "gamma-chunk"],
        ))
    finally:
        contextualizer.get_settings = orig_settings
        contextualizer.generate_text = orig_generate

    assert result == ["context for alpha-chunk", "context for beta-chunk", "context for gamma-chunk"], result


# ── contextualize_chunks: per-chunk failure isolation ───────────────────────────

def test_one_chunk_failure_yields_empty_string_only_for_that_chunk():
    orig_settings = contextualizer.get_settings
    orig_generate = contextualizer.generate_text
    contextualizer.get_settings = lambda: _FakeSettings(True)

    async def flaky(prompt, cached_prefix=None, temperature=0.0, max_tokens=150):
        if "bad-chunk" in prompt:
            raise RuntimeError("simulated API failure")
        return "ok-context"
    contextualizer.generate_text = flaky

    stderr_capture = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr_capture):
            result = asyncio.run(contextualize_chunks(
                "full doc text", ["good-chunk-0", "bad-chunk-1", "good-chunk-2"],
            ))
    finally:
        contextualizer.get_settings = orig_settings
        contextualizer.generate_text = orig_generate

    assert result == ["ok-context", "", "ok-context"], result
    # One stderr line for the failure, mentioning the failed index.
    stderr_output = stderr_capture.getvalue()
    assert "1" in stderr_output and "simulated API failure" in stderr_output, stderr_output


# ── contextualize_chunks: cache-warm order ──────────────────────────────────────

def test_first_chunk_completes_before_any_other_starts():
    """Cache-warm order: chunk 0 is awaited alone (one cache write for the
    document prefix); only after it completes do the rest fan out."""
    orig_settings = contextualizer.get_settings
    orig_generate = contextualizer.generate_text
    contextualizer.get_settings = lambda: _FakeSettings(True)

    chunk_texts = [f"unique-chunk-{i}" for i in range(5)]
    events: list[tuple[str, int]] = []

    async def fake(prompt, cached_prefix=None, temperature=0.0, max_tokens=150):
        idx = next(i for i, t in enumerate(chunk_texts) if t in prompt)
        events.append(("start", idx))
        await asyncio.sleep(0.01)
        events.append(("end", idx))
        return f"ctx-{idx}"

    contextualizer.generate_text = fake
    try:
        result = asyncio.run(contextualize_chunks("full doc text", chunk_texts, concurrency=4))
    finally:
        contextualizer.get_settings = orig_settings
        contextualizer.generate_text = orig_generate

    assert result == [f"ctx-{i}" for i in range(5)], result
    assert events[0] == ("start", 0), events
    assert events[1] == ("end", 0), events
    # No other chunk's "start" event appears before chunk 0's "end".
    other_starts_before_end = [e for e in events[:1] if e[0] == "start" and e[1] != 0]
    assert other_starts_before_end == []
    later_starts = [e for e in events[2:] if e[0] == "start"]
    assert len(later_starts) == 4, events


# ── contextualize_chunks: concurrency bounded by semaphore ──────────────────────

def test_fanout_concurrency_bounded_by_semaphore():
    concurrency = 3
    n_extra = 6  # chunks after the solo first call
    chunk_texts = [f"chunk-{i}" for i in range(1 + n_extra)]

    orig_settings = contextualizer.get_settings
    orig_generate = contextualizer.generate_text
    contextualizer.get_settings = lambda: _FakeSettings(True)

    state = {"in_flight": 0, "max_in_flight": 0, "solo_done": False}
    released = asyncio.Event()

    async def fake(prompt, cached_prefix=None, temperature=0.0, max_tokens=150):
        if not state["solo_done"]:
            # The lone first call (chunk 0) — must not overlap with any other.
            state["solo_done"] = True
            return "solo-context"
        state["in_flight"] += 1
        state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        if state["in_flight"] >= concurrency:
            released.set()
        else:
            await released.wait()
        state["in_flight"] -= 1
        return "fanout-context"

    contextualizer.generate_text = fake
    try:
        result = asyncio.run(contextualize_chunks("full doc text", chunk_texts, concurrency=concurrency))
    finally:
        contextualizer.get_settings = orig_settings
        contextualizer.generate_text = orig_generate

    assert result[0] == "solo-context", result
    assert all(r == "fanout-context" for r in result[1:]), result
    assert state["max_in_flight"] == concurrency, state


# ── contextualize_chunks: cached_prefix passed matches build_document_prefix ────

def test_cached_prefix_matches_document_prefix_for_every_call():
    orig_settings = contextualizer.get_settings
    orig_generate = contextualizer.generate_text
    contextualizer.get_settings = lambda: _FakeSettings(True)

    full_text = "The whole document text goes here."
    expected_prefix = build_document_prefix(full_text)
    captured_prefixes = []

    async def fake(prompt, cached_prefix=None, temperature=0.0, max_tokens=150):
        captured_prefixes.append(cached_prefix)
        return "ctx"

    contextualizer.generate_text = fake
    try:
        asyncio.run(contextualize_chunks(full_text, ["c0", "c1", "c2"]))
    finally:
        contextualizer.get_settings = orig_settings
        contextualizer.generate_text = orig_generate

    assert len(captured_prefixes) == 3
    assert all(p == expected_prefix for p in captured_prefixes), captured_prefixes


# ── generate_text: cached_prefix block structure (pure helper) ─────────────────

def test_generate_text_user_content_none_returns_plain_prompt():
    result = anthropic_client._generate_text_user_content("hello", None)
    assert result == "hello", result


def test_generate_text_user_content_with_prefix_returns_two_blocks():
    result = anthropic_client._generate_text_user_content("chunk prompt", "document text")
    assert result == [
        {"type": "text", "text": "document text", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "chunk prompt"},
    ], result


# ── generate_text: cached_prefix end-to-end against a fake Anthropic client ────

class _FakeMessages:
    def __init__(self):
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="the answer")])


class _FakeAnthropicClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_generate_text_sends_cache_control_block_when_cached_prefix_set():
    fake_client = _FakeAnthropicClient()
    orig_settings = anthropic_client._load_ai_settings
    orig_get_client = anthropic_client._get_anthropic_client
    anthropic_client._load_ai_settings = lambda: {
        "main_model": "claude-opus-4-6", "fast_model": "claude-haiku-4-5-20251001",
        "anthropic_api_key": "", "openai_api_key": "",
    }
    anthropic_client._get_anthropic_client = lambda ai_settings: fake_client
    try:
        result = asyncio.run(anthropic_client.generate_text(
            "chunk prompt", cached_prefix="document text", temperature=0.0, max_tokens=150,
        ))
    finally:
        anthropic_client._load_ai_settings = orig_settings
        anthropic_client._get_anthropic_client = orig_get_client

    assert result == "the answer", result
    sent_messages = fake_client.messages.last_kwargs["messages"]
    assert sent_messages == [{
        "role": "user",
        "content": [
            {"type": "text", "text": "document text", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "chunk prompt"},
        ],
    }], sent_messages


def test_generate_text_default_cached_prefix_is_byte_identical_to_plain_prompt():
    """cached_prefix=None (the default) must send exactly the same request
    shape as generate_text sent before this parameter existed."""
    fake_client = _FakeAnthropicClient()
    orig_settings = anthropic_client._load_ai_settings
    orig_get_client = anthropic_client._get_anthropic_client
    anthropic_client._load_ai_settings = lambda: {
        "main_model": "claude-opus-4-6", "fast_model": "claude-haiku-4-5-20251001",
        "anthropic_api_key": "", "openai_api_key": "",
    }
    anthropic_client._get_anthropic_client = lambda ai_settings: fake_client
    try:
        asyncio.run(anthropic_client.generate_text("plain prompt"))
    finally:
        anthropic_client._load_ai_settings = orig_settings
        anthropic_client._get_anthropic_client = orig_get_client

    sent_messages = fake_client.messages.last_kwargs["messages"]
    assert sent_messages == [{"role": "user", "content": "plain prompt"}], sent_messages


# ── Test runner ────────────────────────────────────────────────────────────────

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
    print("\nRunning contextualizer (Contextual Retrieval) tests...\n")

    _run("build_document_prefix: short doc untruncated", test_build_document_prefix_wraps_short_doc_untruncated)
    _run("build_document_prefix: long doc truncated with marker", test_build_document_prefix_truncates_long_doc_with_marker)
    _run("build_document_prefix: exactly at cap untruncated", test_build_document_prefix_exactly_at_cap_untruncated)
    _run("build_context_prompt: chunk + instruction", test_build_context_prompt_contains_chunk_and_instruction)
    _run("combine: with context prepends", test_combine_with_context_prepends_and_blank_line_separates)
    _run("combine: without context unchanged", test_combine_without_context_returns_content_unchanged)
    _run("flag off -> all empty, no generate_text call", test_flag_off_returns_all_empty_without_calling_generate_text)
    _run("empty chunk_texts -> [], no generate_text call", test_empty_chunk_texts_returns_empty_list_without_calling_generate_text)
    _run("contexts aligned to chunk order and stripped", test_contexts_aligned_to_chunk_order_and_stripped)
    _run("one chunk failure -> empty string only for that chunk", test_one_chunk_failure_yields_empty_string_only_for_that_chunk)
    _run("first chunk completes before any other starts (cache warm order)", test_first_chunk_completes_before_any_other_starts)
    _run("fan-out concurrency bounded by semaphore", test_fanout_concurrency_bounded_by_semaphore)
    _run("cached_prefix matches build_document_prefix for every call", test_cached_prefix_matches_document_prefix_for_every_call)
    _run("_generate_text_user_content: None -> plain prompt", test_generate_text_user_content_none_returns_plain_prompt)
    _run("_generate_text_user_content: prefix -> two blocks", test_generate_text_user_content_with_prefix_returns_two_blocks)
    _run("generate_text: cache_control block sent when cached_prefix set", test_generate_text_sends_cache_control_block_when_cached_prefix_set)
    _run("generate_text: default cached_prefix byte-identical to plain prompt", test_generate_text_default_cached_prefix_is_byte_identical_to_plain_prompt)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
