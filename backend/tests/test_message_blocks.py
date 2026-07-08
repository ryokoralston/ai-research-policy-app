"""Unit tests for serialize_content_blocks (multi-turn tool-use history replay).

No Claude API / network calls — all tests exercise the Python function directly.
Run from the backend directory:
    ./venv/bin/python -m tests.test_message_blocks

Uses a plain assert-based runner, matching tests/test_reminder_tools.py.
"""
import os
import sys
import types

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from services.anthropic_client import serialize_content_blocks


# ── Test runner helpers ───────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run(name: str, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")


# ── serialize_content_blocks tests ────────────────────────────────────────────

def test_sdk_style_text_block():
    """SDK-style object (attribute access) text block serializes to a plain dict."""
    block = types.SimpleNamespace(type="text", text="Hello there.")
    result = serialize_content_blocks([block])
    assert result == [{"type": "text", "text": "Hello there."}], result


def test_sdk_style_tool_use_block():
    """SDK-style tool_use block: only the whitelisted fields survive."""
    block = types.SimpleNamespace(
        type="tool_use", id="toolu_1", name="search_documents",
        input={"query": "EU AI Act"}, extra_field="should not appear",
    )
    result = serialize_content_blocks([block])
    assert result == [{
        "type": "tool_use", "id": "toolu_1", "name": "search_documents",
        "input": {"query": "EU AI Act"},
    }], result


def test_plain_dict_blocks():
    """Plain dicts (key access) round-trip the same as SDK objects."""
    blocks = [
        {"type": "text", "text": "Some text."},
        {"type": "tool_use", "id": "toolu_2", "name": "get_current_datetime", "input": {}},
    ]
    result = serialize_content_blocks(blocks)
    assert result == [
        {"type": "text", "text": "Some text."},
        {"type": "tool_use", "id": "toolu_2", "name": "get_current_datetime", "input": {}},
    ], result


def test_empty_text_block_skipped():
    """Empty/whitespace-only text blocks are dropped — the API rejects them on replay."""
    blocks = [
        {"type": "text", "text": ""},
        {"type": "text", "text": "   "},
        {"type": "text", "text": "real content"},
    ]
    result = serialize_content_blocks(blocks)
    assert result == [{"type": "text", "text": "real content"}], result


def test_unknown_block_type_skipped():
    """Unknown block types (e.g. 'thinking') are dropped, not replayed."""
    blocks = [
        {"type": "thinking", "thinking": "internal reasoning"},
        {"type": "text", "text": "final answer"},
    ]
    result = serialize_content_blocks(blocks)
    assert result == [{"type": "text", "text": "final answer"}], result


def test_mixed_sdk_and_dict_blocks():
    """A content list mixing SDK objects and plain dicts (tool_result replay
    messages are already plain dicts; assistant messages are SDK objects)."""
    blocks = [
        types.SimpleNamespace(type="text", text="Let me check."),
        {"type": "tool_use", "id": "toolu_3", "name": "search_documents", "input": {"query": "q"}},
    ]
    result = serialize_content_blocks(blocks)
    assert result == [
        {"type": "text", "text": "Let me check."},
        {"type": "tool_use", "id": "toolu_3", "name": "search_documents", "input": {"query": "q"}},
    ], result


def test_tool_use_field_whitelist_drops_unknown_attrs():
    """tool_use blocks only keep type/id/name/input even when the source object
    carries other SDK-internal attributes."""
    block = types.SimpleNamespace(
        type="tool_use", id="toolu_4", name="set_reminder",
        input={"content": "x", "timestamp": "2026-06-26T09:00:00"},
        cache_control=None, model_extra={"foo": "bar"},
    )
    result = serialize_content_blocks([block])
    assert list(result[0].keys()) == ["type", "id", "name", "input"], result


def test_empty_content_list():
    """An empty content list serializes to an empty list."""
    assert serialize_content_blocks([]) == []


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\nRunning message-blocks tests...\n")

    _run("serialize_content_blocks: SDK-style text block", test_sdk_style_text_block)
    _run("serialize_content_blocks: SDK-style tool_use block", test_sdk_style_tool_use_block)
    _run("serialize_content_blocks: plain dict blocks", test_plain_dict_blocks)
    _run("serialize_content_blocks: empty text block skipped", test_empty_text_block_skipped)
    _run("serialize_content_blocks: unknown block type skipped", test_unknown_block_type_skipped)
    _run("serialize_content_blocks: mixed SDK and dict blocks", test_mixed_sdk_and_dict_blocks)
    _run("serialize_content_blocks: tool_use field whitelist", test_tool_use_field_whitelist_drops_unknown_attrs)
    _run("serialize_content_blocks: empty content list", test_empty_content_list)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
