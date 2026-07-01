"""Tests for SSE event formatting and the shared queue-to-stream generator.

Run from the backend directory:
    ./venv/bin/python -m tests.test_sse
"""
import asyncio
import json
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from services.anthropic_client import sse_event
from utils.sse import HEARTBEAT_EVENT, is_terminal_event, queue_event_stream


def test_basic_format():
    event = sse_event("status", {"message": "working"})
    assert event == 'event: status\ndata: {"message": "working"}\n\n', repr(event)


def test_data_is_json_round_trippable():
    payload = {"text": "line1\nline2", "count": 3, "nested": {"ok": True}}
    event = sse_event("token", payload)
    lines = event.split("\n")
    assert lines[0] == "event: token"
    assert lines[1].startswith("data: ")
    assert json.loads(lines[1][len("data: "):]) == payload


def test_newlines_in_payload_stay_on_one_data_line():
    """json.dumps escapes newlines, so the SSE framing (blank-line terminator)
    can never be broken by streamed token text."""
    event = sse_event("token", {"text": "a\n\nb"})
    # exactly one data line, terminated by the double newline
    assert event.count("\ndata: ") == 1
    assert event.endswith("\n\n")
    assert "\n\n" not in event[:-2], "payload newlines must be escaped"


def test_complete_event_contains_event_type_marker():
    """The stream terminators rely on event_type in the payload — pin it."""
    event = sse_event("complete", {"session_id": "x", "event_type": "complete"})
    assert '"event_type": "complete"' in event


# ── queue_event_stream ────────────────────────────────────────────────────────

def _collect(events_to_queue, timeout_seconds=5.0):
    async def run():
        queue: asyncio.Queue = asyncio.Queue()
        for e in events_to_queue:
            queue.put_nowait(e)
        return [e async for e in queue_event_stream(queue, timeout_seconds)]
    return asyncio.run(run())


def test_terminal_event_detection():
    assert is_terminal_event(sse_event("complete", {"event_type": "complete"}))
    assert is_terminal_event('event: error\ndata: {"message": "boom"}\n\n')
    assert not is_terminal_event(sse_event("token", {"text": "hi"}))
    assert not is_terminal_event(sse_event("status", {"message": "working"}))
    assert not is_terminal_event("data: no event name\n\n")


def test_stream_relays_until_complete():
    events = [
        sse_event("status", {"message": "working"}),
        sse_event("token", {"text": "abc"}),
        sse_event("complete", {"event_type": "complete"}),
        sse_event("token", {"text": "never sent"}),  # after terminal — dropped
    ]
    out = _collect(events)
    assert len(out) == 3, out
    assert out[-1].startswith("event: complete")


def test_error_without_event_type_field_terminates():
    """Regression: research error events carry no event_type in the payload;
    the old substring check missed them and the stream heartbeated forever."""
    events = ['event: error\ndata: {"message": "pipeline failed"}\n\n']
    out = _collect(events)
    assert len(out) == 1, out
    assert out[0].startswith("event: error")


def test_heartbeat_on_idle_queue():
    async def run():
        queue: asyncio.Queue = asyncio.Queue()
        gen = queue_event_stream(queue, timeout_seconds=0.01)
        first = await gen.__anext__()          # idle → heartbeat
        queue.put_nowait(sse_event("complete", {"event_type": "complete"}))
        second = await gen.__anext__()
        return first, second
    first, second = asyncio.run(run())
    assert first == HEARTBEAT_EVENT, repr(first)
    assert second.startswith("event: complete")


def test_token_payload_mentioning_complete_does_not_terminate():
    """Streamed text that talks about completion must not end the stream —
    json.dumps escaping plus name-based detection keep it safe."""
    events = [
        sse_event("token", {"text": 'the "event_type": "complete" marker'}),
        sse_event("complete", {"event_type": "complete"}),
    ]
    out = _collect(events)
    assert len(out) == 2, out


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
    print("\nRunning SSE format tests...\n")

    _run("basic format", test_basic_format)
    _run("data is JSON round-trippable", test_data_is_json_round_trippable)
    _run("payload newlines stay on one data line", test_newlines_in_payload_stay_on_one_data_line)
    _run("complete event contains event_type marker", test_complete_event_contains_event_type_marker)
    _run("terminal event detection", test_terminal_event_detection)
    _run("stream relays until complete", test_stream_relays_until_complete)
    _run("error without event_type terminates", test_error_without_event_type_field_terminates)
    _run("heartbeat on idle queue", test_heartbeat_on_idle_queue)
    _run("token mentioning complete does not terminate", test_token_payload_mentioning_complete_does_not_terminate)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
