"""Tests for services/anthropic_client._thinking_stream_tuple — the pure
event -> (kind, text) dispatch function used by stream_text_with_thinking.

Uses plain types.SimpleNamespace fake event objects — no API calls, no
network. Run from the backend directory:
    ./venv/bin/python -m tests.test_thinking_stream
"""
import os
import sys
import types

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from services.anthropic_client import _thinking_stream_tuple, _thinking_message_content


def _event(etype, delta=None):
    return types.SimpleNamespace(type=etype, delta=delta)


# ── thinking_delta -> ("thinking", text) ────────────────────────────────────

def test_thinking_delta_maps_to_thinking_tuple():
    delta = types.SimpleNamespace(type="thinking_delta", thinking="Considering the tradeoffs")
    event = _event("content_block_delta", delta=delta)
    result = _thinking_stream_tuple(event)
    assert result == ("thinking", "Considering the tradeoffs"), result


# ── text_delta -> ("text", text) ────────────────────────────────────────────

def test_text_delta_maps_to_text_tuple():
    delta = types.SimpleNamespace(type="text_delta", text="Frontier models should be regulated")
    event = _event("content_block_delta", delta=delta)
    result = _thinking_stream_tuple(event)
    assert result == ("text", "Frontier models should be regulated"), result


# ── Unrelated event types -> None ───────────────────────────────────────────

def test_message_start_returns_none():
    event = _event("message_start")
    assert _thinking_stream_tuple(event) is None


def test_content_block_start_returns_none():
    content_block = types.SimpleNamespace(type="text")
    event = types.SimpleNamespace(type="content_block_start", content_block=content_block)
    assert _thinking_stream_tuple(event) is None


def test_message_delta_returns_none():
    event = _event("message_delta")
    assert _thinking_stream_tuple(event) is None


def test_content_block_stop_returns_none():
    event = _event("content_block_stop")
    assert _thinking_stream_tuple(event) is None


def test_unknown_delta_type_within_content_block_delta_returns_none():
    # e.g. an input_json delta on a tool_use block — not thinking or text
    delta = types.SimpleNamespace(type="input_json_delta", partial_json="{}")
    event = _event("content_block_delta", delta=delta)
    assert _thinking_stream_tuple(event) is None


# ── Empty delta text handling ────────────────────────────────────────────────
# Chosen behavior: the pure dispatch function still returns a tuple with an
# empty string (dispatch, not filtering) — this matters on models where
# thinking.display defaults to "omitted" and thinking blocks stream with
# empty .thinking text. Filtering empty text out of the SSE stream is the
# caller's job (stream_text_with_thinking), not this function's.

def test_empty_thinking_text_still_returns_thinking_tuple():
    delta = types.SimpleNamespace(type="thinking_delta", thinking="")
    event = _event("content_block_delta", delta=delta)
    result = _thinking_stream_tuple(event)
    assert result == ("thinking", ""), result


def test_empty_text_delta_still_returns_text_tuple():
    delta = types.SimpleNamespace(type="text_delta", text="")
    event = _event("content_block_delta", delta=delta)
    result = _thinking_stream_tuple(event)
    assert result == ("text", ""), result


def test_missing_thinking_attr_defaults_to_empty_string():
    # Defensive case: a delta object with no .thinking attribute at all.
    delta = types.SimpleNamespace(type="thinking_delta")
    event = _event("content_block_delta", delta=delta)
    result = _thinking_stream_tuple(event)
    assert result == ("thinking", ""), result


# ── _thinking_message_content ───────────────────────────────────────────────
# Pure message-content construction used by stream_text_with_thinking's
# cached_context feature — no live API call needed.

def test_no_cached_context_returns_plain_prompt_string():
    result = _thinking_message_content("What are the key risks?", None)
    assert result == "What are the key risks?", result


def test_cached_context_returns_two_blocks_with_cache_control_on_first_only():
    result = _thinking_message_content("Write section X.", "shared source material")
    assert isinstance(result, list), result
    assert len(result) == 2, result

    first, second = result
    assert first == {
        "type": "text",
        "text": "shared source material",
        "cache_control": {"type": "ephemeral"},
    }, first
    assert second == {"type": "text", "text": "Write section X."}, second
    assert "cache_control" not in second, second


def test_cached_context_empty_string_still_builds_two_blocks():
    # "" is falsy but distinct from None — must still take the two-block path
    # (the caller's contract is "is cached_context is None", not truthiness).
    result = _thinking_message_content("prompt text", "")
    assert isinstance(result, list), result
    assert result[0]["text"] == "", result
    assert result[0]["cache_control"] == {"type": "ephemeral"}, result
    assert result[1] == {"type": "text", "text": "prompt text"}, result


# ── Test runner ──────────────────────────────────────────────────────────────

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
    print("\nRunning thinking_stream tests...\n")

    _run("thinking_delta -> ('thinking', text)", test_thinking_delta_maps_to_thinking_tuple)
    _run("text_delta -> ('text', text)", test_text_delta_maps_to_text_tuple)
    _run("message_start -> None", test_message_start_returns_none)
    _run("content_block_start -> None", test_content_block_start_returns_none)
    _run("message_delta -> None", test_message_delta_returns_none)
    _run("content_block_stop -> None", test_content_block_stop_returns_none)
    _run("unknown delta type -> None", test_unknown_delta_type_within_content_block_delta_returns_none)
    _run("empty thinking text -> ('thinking', '')", test_empty_thinking_text_still_returns_thinking_tuple)
    _run("empty text delta -> ('text', '')", test_empty_text_delta_still_returns_text_tuple)
    _run("missing .thinking attr -> ('thinking', '')", test_missing_thinking_attr_defaults_to_empty_string)

    _run("no cached_context -> plain prompt string", test_no_cached_context_returns_plain_prompt_string)
    _run("cached_context -> two blocks, cache_control on first only", test_cached_context_returns_two_blocks_with_cache_control_on_first_only)
    _run("cached_context='' still builds two blocks", test_cached_context_empty_string_still_builds_two_blocks)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
