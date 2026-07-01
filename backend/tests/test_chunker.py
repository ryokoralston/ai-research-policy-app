"""Tests for rag/chunker.py — pure text chunking, no external deps.

Run from the backend directory:
    ./venv/bin/python -m tests.test_chunker
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from rag.chunker import _approx_tokens, _detect_heading, chunk_text, chunk_plain_text


def _words(n: int, word: str = "policy") -> str:
    return " ".join(word for _ in range(n))


# ── Heading detection ─────────────────────────────────────────────────────────

def test_detect_heading():
    assert _detect_heading("1. Introduction to AI Policy")
    assert _detect_heading("2) Regulatory Landscape")
    assert _detect_heading("EXECUTIVE SUMMARY")
    assert not _detect_heading("this is a normal sentence.")
    assert not _detect_heading("")
    assert not _detect_heading("A" * 121)  # too long
    assert not _detect_heading("AB")       # too short for the all-caps rule
    # all-caps but more than 8 words
    assert not _detect_heading("ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE")


# ── chunk_text ────────────────────────────────────────────────────────────────

def test_short_text_below_min_tokens_is_dropped():
    # MIN_TOKENS=100 → ~77 words; 30 words must produce no chunks
    chunks = chunk_text(_words(30))
    assert chunks == [], f"expected no chunks, got {len(chunks)}"


def test_single_chunk_with_default_section():
    chunks = chunk_text(_words(120))
    assert len(chunks) == 1, len(chunks)
    assert chunks[0].section_header == "Introduction"
    assert chunks[0].page_number == 1
    assert chunks[0].chunk_index == 0


def test_heading_becomes_section_header():
    text = "REGULATORY LANDSCAPE\n" + _words(150)
    chunks = chunk_text(text)
    assert len(chunks) == 1, len(chunks)
    assert chunks[0].section_header == "REGULATORY LANDSCAPE"
    # The heading line itself is not part of the chunk body
    assert "REGULATORY LANDSCAPE" not in chunks[0].content


def test_long_text_splits_with_overlap():
    # 10 paragraphs x 120 words ≈ 1560 tokens → multiple chunks (MAX=800)
    paragraphs = [_words(120, f"w{i}") for i in range(10)]
    chunks = chunk_text("\n\n".join(paragraphs))
    assert len(chunks) >= 2, f"expected multiple chunks, got {len(chunks)}"
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    # Overlap: the last paragraph of chunk N reappears at the start of chunk N+1
    for prev, nxt in zip(chunks, chunks[1:]):
        last_para = prev.content.split("\n\n")[-1]
        assert nxt.content.startswith(last_para), "expected 1-paragraph overlap"
    # No chunk wildly exceeds MAX_TOKENS (one paragraph past the boundary is allowed)
    for c in chunks:
        assert c.token_count <= 800 + _approx_tokens(_words(120)), c.token_count


def test_chunk_plain_text_returns_word_count():
    text = _words(200)
    chunks, word_count = chunk_plain_text(text)
    assert word_count == 200, word_count
    assert len(chunks) == 1, len(chunks)


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
    print("\nRunning chunker tests...\n")

    _run("heading detection heuristics", test_detect_heading)
    _run("short text below min tokens is dropped", test_short_text_below_min_tokens_is_dropped)
    _run("single chunk gets default section", test_single_chunk_with_default_section)
    _run("heading becomes section header", test_heading_becomes_section_header)
    _run("long text splits with overlap", test_long_text_splits_with_overlap)
    _run("chunk_plain_text returns word count", test_chunk_plain_text_returns_word_count)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
