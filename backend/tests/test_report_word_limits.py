"""Tests for word-limit extraction and per-section budgets (report_generator).

Run from the backend directory:
    ./venv/bin/python -m tests.test_report_word_limits
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from services.report_generator import _extract_word_limit, _calculate_word_budgets


# ── _extract_word_limit ───────────────────────────────────────────────────────

def test_english_patterns():
    assert _extract_word_limit("200 words or less") == 200
    assert _extract_word_limit("keep it under 300 words") == 300
    assert _extract_word_limit("max 150 words please") == 150
    assert _extract_word_limit("at most 500 words") == 500
    assert _extract_word_limit("1000 words total") == 1000


def test_japanese_patterns():
    assert _extract_word_limit("300語以内でお願いします") == 300
    assert _extract_word_limit("500ワード以下") == 500
    assert _extract_word_limit("400 words 以内") == 400


def test_no_limit_returns_none():
    assert _extract_word_limit(None) is None
    assert _extract_word_limit("") is None
    assert _extract_word_limit("focus on the EU AI Act") is None
    assert _extract_word_limit("use simple words") is None


# ── _calculate_word_budgets ───────────────────────────────────────────────────

_SECTIONS = [
    {"key": "a", "title": "A", "instructions": "Write 100-200 words on background."},
    {"key": "b", "title": "B", "instructions": "About 300 words of analysis."},
    {"key": "c", "title": "C", "instructions": "No explicit count here."},  # default 80
]


def test_budgets_none_without_limit():
    assert _calculate_word_budgets(_SECTIONS, None) is None
    assert _calculate_word_budgets(_SECTIONS, "no limit mentioned") is None


def test_budgets_are_proportional():
    budgets = _calculate_word_budgets(_SECTIONS, "265 words or less")
    # defaults: (100+200)//2=150, 300, 80 → total 530; limit 265 = half
    assert budgets == [75, 150, 40], budgets


def test_budgets_have_floor():
    budgets = _calculate_word_budgets(_SECTIONS, "30 words max")
    assert all(b >= 15 for b in budgets), budgets


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
    print("\nRunning word-limit tests...\n")

    _run("English word-limit patterns", test_english_patterns)
    _run("Japanese word-limit patterns", test_japanese_patterns)
    _run("no limit returns None", test_no_limit_returns_none)
    _run("budgets None without limit", test_budgets_none_without_limit)
    _run("budgets are proportional", test_budgets_are_proportional)
    _run("budgets have a floor", test_budgets_have_floor)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
