"""Tests for SSE event formatting (services/anthropic_client.sse_event).

Run from the backend directory:
    ./venv/bin/python -m tests.test_sse
"""
import json
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from services.anthropic_client import sse_event


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

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
