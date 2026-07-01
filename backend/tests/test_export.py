"""Tests for utils/export.py — Markdown → plain text conversion.

Run from the backend directory:
    ./venv/bin/python -m tests.test_export
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from utils.export import markdown_to_plain


def test_headings_stripped():
    assert markdown_to_plain("# Title\n## Sub\ntext") == "Title\nSub\ntext"


def test_bold_and_italic_unwrapped():
    assert markdown_to_plain("**bold** and *italic*") == "bold and italic"


def test_code_blocks_removed_inline_code_unwrapped():
    assert markdown_to_plain("before\n```\ncode\n```\nafter `x` end") == "before\n\nafter x end"


def test_links_keep_text():
    assert markdown_to_plain("see [the act](https://example.com) here") == "see the act here"


def test_bullets_normalized():
    assert markdown_to_plain("- one\n* two") == "- one\n- two"


def test_combined_document():
    md = "# Report\n\n**Key** finding with [source](http://x).\n\n- item"
    assert markdown_to_plain(md) == "Report\n\nKey finding with source.\n\n- item"


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
    print("\nRunning export tests...\n")

    _run("headings stripped", test_headings_stripped)
    _run("bold and italic unwrapped", test_bold_and_italic_unwrapped)
    _run("code blocks removed, inline code unwrapped", test_code_blocks_removed_inline_code_unwrapped)
    _run("links keep text", test_links_keep_text)
    _run("bullets normalized", test_bullets_normalized)
    _run("combined document", test_combined_document)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
