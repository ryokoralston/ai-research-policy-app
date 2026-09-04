"""Tests for the max_tokens truncation sentinel in
services/anthropic_client.py::stream_text_with_thinking.

max_tokens is shared with adaptive-thinking tokens, so a long generation can
be cut off mid-sentence. Before this sentinel existed, a truncated response
was byte-indistinguishable from a complete one to every caller: nothing
inspected final.stop_reason. A whole-report revision that got cut short was
then re-graded and adopted, silently replacing a complete report with a
fragment (see services/report_quality.py and tests/test_report_revision.py).

The Anthropic client is faked end to end — client.messages.stream(...) returns
an async context manager that is itself async-iterable over raw stream events
and exposes get_final_message(). _get_anthropic_client / _load_ai_settings are
monkeypatched, so no API key, no network, no cost.

Run from the backend directory:
    ./venv/bin/python -m tests.test_stream_truncation
"""
import asyncio
import os
import sys
import types

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

import services.anthropic_client as anthropic_client
from services.anthropic_client import stream_text_with_thinking


# ── Fakes ─────────────────────────────────────────────────────────────────────

def _text_delta_event(text):
    return types.SimpleNamespace(
        type="content_block_delta",
        delta=types.SimpleNamespace(type="text_delta", text=text),
    )


class _FakeStream:
    """Async context manager + async iterator, matching the shape
    stream_text_with_thinking uses: `async with client.messages.stream(**kw)
    as stream: async for event in stream: ...` then
    `await stream.get_final_message()`."""

    def __init__(self, events, stop_reason):
        self._events = events
        self._stop_reason = stop_reason

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e
        return gen()

    async def get_final_message(self):
        # usage=None on purpose: the truncation check must not depend on
        # usage being recordable (record_anthropic is skipped when it's None).
        return types.SimpleNamespace(stop_reason=self._stop_reason, usage=None)


class _FakeMessages:
    def __init__(self, events, stop_reason, recorded_kwargs):
        self._events = events
        self._stop_reason = stop_reason
        self._recorded_kwargs = recorded_kwargs

    def stream(self, **kwargs):
        self._recorded_kwargs.append(kwargs)
        return _FakeStream(self._events, self._stop_reason)


class _FakeClient:
    def __init__(self, events, stop_reason, recorded_kwargs):
        self.messages = _FakeMessages(events, stop_reason, recorded_kwargs)


def _collect(events, stop_reason, **stream_kwargs):
    """Drive stream_text_with_thinking against a faked client and return
    (yielded_tuples, kwargs_passed_to_messages_stream)."""
    recorded_kwargs: list[dict] = []
    orig = (anthropic_client._get_anthropic_client, anthropic_client._load_ai_settings)
    anthropic_client._get_anthropic_client = lambda ai_settings: _FakeClient(
        events, stop_reason, recorded_kwargs
    )
    anthropic_client._load_ai_settings = lambda: {"main_model": "claude-test-model"}
    try:
        async def run():
            return [t async for t in stream_text_with_thinking("prompt", **stream_kwargs)]
        out = asyncio.run(run())
    finally:
        anthropic_client._get_anthropic_client, anthropic_client._load_ai_settings = orig
    return out, recorded_kwargs


# ── (a) stop_reason == "max_tokens" → sentinel is the last item ──────────────

def test_max_tokens_stop_reason_yields_truncated_sentinel_last():
    out, _ = _collect(
        [_text_delta_event("first half "), _text_delta_event("cut off mid-sen")],
        "max_tokens",
    )
    assert out[-1] == ("truncated", ""), out
    assert out[:-1] == [("text", "first half "), ("text", "cut off mid-sen")], out
    # Exactly one sentinel, and it never displaces real text.
    assert [k for k, _ in out].count("truncated") == 1, out


def test_truncated_sentinel_yielded_even_when_no_text_streamed():
    """All of max_tokens burned on thinking: zero text deltas, still cut off.
    The sentinel must not be gated behind the `if text:` filter in the loop."""
    out, _ = _collect([], "max_tokens")
    assert out == [("truncated", "")], out


# ── (b) normal completion → no sentinel ──────────────────────────────────────

def test_end_turn_stop_reason_yields_no_sentinel():
    out, _ = _collect(
        [_text_delta_event("a complete answer.")],
        "end_turn",
    )
    assert out == [("text", "a complete answer.")], out
    assert all(kind != "truncated" for kind, _ in out), out


def test_missing_stop_reason_yields_no_sentinel():
    """A final message with stop_reason=None (or an SDK object lacking the
    attribute) must not be reported as truncated."""
    out, _ = _collect([_text_delta_event("done.")], None)
    assert out == [("text", "done.")], out


# ── (c) max_tokens is actually forwarded to the API call ─────────────────────

def test_max_tokens_argument_is_passed_through_to_the_api_call():
    _, recorded = _collect([_text_delta_event("x")], "end_turn", max_tokens=32000)
    assert len(recorded) == 1, recorded
    assert recorded[0]["max_tokens"] == 32000, recorded[0]


def test_default_max_tokens_is_unchanged():
    _, recorded = _collect([_text_delta_event("x")], "end_turn")
    assert recorded[0]["max_tokens"] == 8192, recorded[0]


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
    print("\nRunning stream_text_with_thinking truncation-sentinel tests...\n")

    _run("max_tokens stop_reason yields ('truncated', '') last", test_max_tokens_stop_reason_yields_truncated_sentinel_last)
    _run("sentinel yielded even with no text deltas", test_truncated_sentinel_yielded_even_when_no_text_streamed)
    _run("end_turn yields no sentinel", test_end_turn_stop_reason_yields_no_sentinel)
    _run("missing stop_reason yields no sentinel", test_missing_stop_reason_yields_no_sentinel)
    _run("max_tokens argument forwarded to the API call", test_max_tokens_argument_is_passed_through_to_the_api_call)
    _run("default max_tokens unchanged", test_default_max_tokens_is_unchanged)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
