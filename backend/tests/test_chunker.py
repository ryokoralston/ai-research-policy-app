"""Tests for rag/chunker.py — pure text chunking, no external deps.

Run from the backend directory:
    ./venv/bin/python -m tests.test_chunker
"""
import os
import re
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from rag.chunker import (
    _approx_tokens,
    _detect_heading,
    _clean_markdown_heading,
    MAX_TOKENS,
    MIN_TOKENS,
    chunk_text,
    chunk_plain_text,
)


def _words(n: int, word: str = "policy") -> str:
    return " ".join(word for _ in range(n))


def _sentences(n: int, prefix: str = "Sentence") -> str:
    """n distinct, individually-numbered sentences forming one paragraph."""
    return " ".join(f"{prefix} number {i} contains some filler words here." for i in range(n))


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


def test_detect_heading_markdown_atx_levels():
    assert _detect_heading("# Title")
    assert _detect_heading("## Executive Summary")
    assert _detect_heading("### Sub-section")
    assert _detect_heading("###### Deepest level")
    # No space after hashes -> not a heading (e.g. a hashtag-like token)
    assert not _detect_heading("#hashtag")


def test_clean_markdown_heading_strips_hashes_and_emphasis():
    assert _clean_markdown_heading("## **Executive Summary**") == "Executive Summary"
    assert _clean_markdown_heading("# Title") == "Title"
    assert _clean_markdown_heading("### _Italic Heading_") == "Italic Heading"


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


# ── Gap 1: Markdown heading detection ───────────────────────────────────────────

def test_markdown_heading_becomes_cleaned_section_header():
    text = "## **Executive Summary**\n" + _words(120)
    chunks = chunk_text(text)
    assert len(chunks) == 1, len(chunks)
    assert chunks[0].section_header == "Executive Summary", chunks[0].section_header
    # The raw heading line (with hashes/emphasis) is not part of the chunk body
    assert "##" not in chunks[0].content
    assert "**" not in chunks[0].content


def test_heading_like_line_inside_fence_not_treated_as_heading():
    # A Python "# comment" inside a fenced code block must not become a section,
    # and the fence delimiters/content pass through as ordinary text.
    text = (
        "## Real Heading\n"
        + _words(120) + "\n\n"
        + "```python\n# comment not a heading\nCODE_LINE = 1\n```\n\n"
        + _words(120)
    )
    chunks = chunk_text(text)
    assert len(chunks) >= 1, len(chunks)
    assert all(c.section_header == "Real Heading" for c in chunks), [c.section_header for c in chunks]
    joined = "\n".join(c.content for c in chunks)
    assert "# comment not a heading" in joined, "fenced line should pass through as ordinary content"


# ── Gap 2: no cross-section contamination ───────────────────────────────────────

def test_chunks_never_span_sections():
    alpha = [_words(40, "alpha") for _ in range(3)]
    beta = [_words(40, "beta") for _ in range(3)]
    text = (
        "## Section Alpha\n" + "\n\n".join(alpha) + "\n\n"
        "## Section Beta\n" + "\n\n".join(beta)
    )
    chunks = chunk_text(text)
    assert len(chunks) == 2, f"expected a flush at the section boundary, got {len(chunks)}"
    assert chunks[0].section_header == "Section Alpha", chunks[0].section_header
    assert chunks[1].section_header == "Section Beta", chunks[1].section_header
    assert "alpha" in chunks[0].content and "beta" not in chunks[0].content
    assert "beta" in chunks[1].content and "alpha" not in chunks[1].content


def test_no_overlap_across_section_boundary():
    alpha = [_words(40, "alpha") for _ in range(3)]
    beta = [_words(40, "beta") for _ in range(3)]
    text = (
        "## Section Alpha\n" + "\n\n".join(alpha) + "\n\n"
        "## Section Beta\n" + "\n\n".join(beta)
    )
    chunks = chunk_text(text)
    assert len(chunks) == 2, len(chunks)
    # The first chunk of section 2 must not contain any section-1 text.
    assert "alpha" not in chunks[1].content, "section boundary must not carry overlap"


def test_tiny_section_merges_instead_of_flushing():
    # A section under MIN_TOKENS should merge into the next section's chunk
    # rather than emit a near-empty chunk (documented tradeoff).
    text = (
        "## Tiny Section\n" + _words(5, "tiny") + "\n\n"
        "## Section Beta\n" + "\n\n".join(_words(40, "beta") for _ in range(3))
    )
    chunks = chunk_text(text)
    assert len(chunks) == 1, f"expected the tiny section to merge, got {len(chunks)}"
    assert "tiny" in chunks[0].content
    assert "beta" in chunks[0].content


def test_within_section_overlap_still_works():
    # Same section throughout -> existing size-based overlap behavior is unchanged.
    text = "## One Big Section\n" + "\n\n".join(_words(120, f"w{i}") for i in range(10))
    chunks = chunk_text(text)
    assert len(chunks) >= 2, f"expected multiple chunks, got {len(chunks)}"
    assert all(c.section_header == "One Big Section" for c in chunks)
    for prev, nxt in zip(chunks, chunks[1:]):
        last_para = prev.content.split("\n\n")[-1]
        assert nxt.content.startswith(last_para), "expected 1-paragraph overlap within a section"


# ── Gap 3: sentence-based splitting of oversized paragraphs ────────────────────

def _split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def test_oversized_paragraph_splits_with_sentence_overlap():
    # 120 sentences x ~8 words ≈ 1248 tokens, single paragraph -> well over MAX_TOKENS.
    big_paragraph = _sentences(120)
    text = "## Long Section\n" + big_paragraph
    chunks = chunk_text(text)
    assert len(chunks) >= 2, f"expected the paragraph to split, got {len(chunks)}"
    assert all(c.section_header == "Long Section" for c in chunks)
    # Each piece stays within (roughly) the token budget — TARGET-sized grouping
    # plus at most one overlap sentence, well clear of MAX_TOKENS.
    for c in chunks:
        assert c.token_count <= MAX_TOKENS, c.token_count
    # 1-sentence overlap between consecutive pieces.
    for prev, nxt in zip(chunks, chunks[1:]):
        prev_sentences = _split_sentences(prev.content)
        nxt_sentences = _split_sentences(nxt.content)
        assert prev_sentences[-1] == nxt_sentences[0], "expected 1-sentence overlap"


def test_oversized_paragraph_with_no_sentence_terminators_is_single_chunk():
    # No '.', '!' or '?' anywhere -> cannot be split into sentences; must not
    # infinite-loop and must come back as a single (oversized) chunk.
    blob = " ".join(["word"] * 700)  # ~910 tokens, no punctuation
    text = "## Blob Section\n" + blob
    chunks = chunk_text(text)
    assert len(chunks) == 1, f"expected a single unsplit chunk, got {len(chunks)}"
    assert chunks[0].section_header == "Blob Section"
    assert chunks[0].token_count > MAX_TOKENS, chunks[0].token_count


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
    _run("markdown ATX heading levels detected", test_detect_heading_markdown_atx_levels)
    _run("clean markdown heading strips hashes/emphasis", test_clean_markdown_heading_strips_hashes_and_emphasis)
    _run("short text below min tokens is dropped", test_short_text_below_min_tokens_is_dropped)
    _run("single chunk gets default section", test_single_chunk_with_default_section)
    _run("heading becomes section header", test_heading_becomes_section_header)
    _run("long text splits with overlap", test_long_text_splits_with_overlap)
    _run("chunk_plain_text returns word count", test_chunk_plain_text_returns_word_count)

    _run("markdown heading becomes cleaned section header", test_markdown_heading_becomes_cleaned_section_header)
    _run("heading-like line inside fence is not a heading", test_heading_like_line_inside_fence_not_treated_as_heading)

    _run("chunks never span sections", test_chunks_never_span_sections)
    _run("no overlap across section boundary", test_no_overlap_across_section_boundary)
    _run("tiny section merges instead of flushing", test_tiny_section_merges_instead_of_flushing)
    _run("within-section overlap still works", test_within_section_overlap_still_works)

    _run("oversized paragraph splits with sentence overlap", test_oversized_paragraph_splits_with_sentence_overlap)
    _run("oversized paragraph with no terminators is single chunk", test_oversized_paragraph_with_no_sentence_terminators_is_single_chunk)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
