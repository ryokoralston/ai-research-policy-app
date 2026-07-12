"""Tests for scanned-PDF ingestion fallback: the pure helpers in
services/anthropic_client.py (_pdf_message_content, generate_text_with_pdf's
OpenAI guard) plus the pure predicates in services/rag_service.py
(_pdf_needs_vision_fallback, _within_fallback_guards) and a sanity check on
PDF_TRANSCRIPTION_PROMPT.

No live API calls — generate_text_with_pdf's OpenAI-rejection path raises
before any client is constructed, and _load_ai_settings is patched to avoid
a real DB round-trip.

Run from the backend directory:
    ./venv/bin/python -m tests.test_pdf_fallback
"""
import asyncio
import base64
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

import services.anthropic_client as anthropic_client


# ── _pdf_message_content ──────────────────────────────────────────────────────

def test_pdf_message_content_shape_and_roundtrip():
    pdf_bytes = b"%PDF-1.4\nthis-is-fake-pdf-bytes-for-testing-purposes"
    blocks = anthropic_client._pdf_message_content(pdf_bytes, "Summarize this document")

    assert len(blocks) == 2, blocks
    doc_block, text_block = blocks

    assert doc_block["type"] == "document", doc_block
    assert doc_block["source"]["type"] == "base64", doc_block
    assert doc_block["source"]["media_type"] == "application/pdf", doc_block
    # base64 round-trips to the exact original bytes
    decoded = base64.standard_b64decode(doc_block["source"]["data"])
    assert decoded == pdf_bytes, decoded
    # no embedded newlines (standard_b64encode never inserts them, but pin
    # the behavior explicitly since the API requires a clean base64 string)
    assert "\n" not in doc_block["source"]["data"]

    assert text_block == {"type": "text", "text": "Summarize this document"}, text_block


def test_pdf_message_content_document_block_is_first():
    blocks = anthropic_client._pdf_message_content(b"fake-pdf-bytes", "prompt text")
    assert blocks[0]["type"] == "document", "document block must come before the text block"
    assert blocks[1]["type"] == "text"
    assert blocks[1]["text"] == "prompt text"


# ── generate_text_with_pdf: OpenAI model rejection ────────────────────────────

def test_generate_text_with_pdf_rejects_openai_model():
    original_load = anthropic_client._load_ai_settings
    anthropic_client._load_ai_settings = lambda: {
        "main_model": "claude-opus-4-6",
        "fast_model": "claude-haiku-4-5-20251001",
        "anthropic_api_key": "",
        "openai_api_key": "",
    }
    try:
        raised = False
        try:
            asyncio.run(anthropic_client.generate_text_with_pdf(
                "transcribe this pdf", b"fake-bytes", model="gpt-4o",
            ))
        except ValueError:
            raised = True
        assert raised, "expected ValueError for an OpenAI model"
    finally:
        anthropic_client._load_ai_settings = original_load


def test_generate_text_with_pdf_rejects_openai_default_main_model():
    """Same guard, but relying on the default (ai_settings['main_model']) being
    an OpenAI model rather than an explicit model= argument."""
    original_load = anthropic_client._load_ai_settings
    anthropic_client._load_ai_settings = lambda: {
        "main_model": "gpt-4o",
        "fast_model": "gpt-4o-mini",
        "anthropic_api_key": "",
        "openai_api_key": "",
    }
    try:
        raised = False
        try:
            asyncio.run(anthropic_client.generate_text_with_pdf(
                "transcribe this pdf", b"fake-bytes",
            ))
        except ValueError:
            raised = True
        assert raised, "expected ValueError when the default main_model is an OpenAI model"
    finally:
        anthropic_client._load_ai_settings = original_load


# ── _pdf_needs_vision_fallback ─────────────────────────────────────────────────

def test_needs_fallback_empty_chunks_is_true():
    from services.rag_service import _pdf_needs_vision_fallback
    assert _pdf_needs_vision_fallback([], word_count=0, page_count=10) is True


def test_needs_fallback_dense_text_is_false():
    from services.rag_service import _pdf_needs_vision_fallback
    # 300 words/page over 10 pages -> well above MIN_WORDS_PER_PAGE
    assert _pdf_needs_vision_fallback(["chunk"], word_count=3000, page_count=10) is False


def test_needs_fallback_sparse_text_is_true():
    from services.rag_service import _pdf_needs_vision_fallback
    # 5 words/page over 10 pages -> below MIN_WORDS_PER_PAGE
    assert _pdf_needs_vision_fallback(["chunk"], word_count=50, page_count=10) is True


def test_needs_fallback_zero_page_count_does_not_crash():
    from services.rag_service import _pdf_needs_vision_fallback
    # Non-empty chunks but page_count == 0 must not raise ZeroDivisionError,
    # and is treated as needing the fallback (can't trust the word/page ratio).
    assert _pdf_needs_vision_fallback(["chunk"], word_count=100, page_count=0) is True


def test_needs_fallback_boundary_at_threshold():
    from services.rag_service import _pdf_needs_vision_fallback, MIN_WORDS_PER_PAGE
    # Exactly at the threshold -> not below it -> False (dense enough)
    at_threshold = MIN_WORDS_PER_PAGE * 10
    assert _pdf_needs_vision_fallback(["chunk"], word_count=at_threshold, page_count=10) is False
    # One word under the threshold -> True
    assert _pdf_needs_vision_fallback(["chunk"], word_count=at_threshold - 1, page_count=10) is True


# ── _within_fallback_guards ─────────────────────────────────────────────────────

def test_within_fallback_guards_small_short_pdf_passes():
    from services.rag_service import _within_fallback_guards, MAX_FALLBACK_FILE_BYTES, MAX_FALLBACK_PAGES
    assert _within_fallback_guards(1024, 5) is True
    assert _within_fallback_guards(MAX_FALLBACK_FILE_BYTES, MAX_FALLBACK_PAGES) is True


def test_within_fallback_guards_oversized_file_fails():
    from services.rag_service import _within_fallback_guards, MAX_FALLBACK_FILE_BYTES
    assert _within_fallback_guards(MAX_FALLBACK_FILE_BYTES + 1, 5) is False


def test_within_fallback_guards_too_many_pages_fails():
    from services.rag_service import _within_fallback_guards, MAX_FALLBACK_PAGES
    assert _within_fallback_guards(1024, MAX_FALLBACK_PAGES + 1) is False


# ── PDF_TRANSCRIPTION_PROMPT sanity ────────────────────────────────────────────

def test_transcription_prompt_mentions_page_heading():
    from services.rag_service import PDF_TRANSCRIPTION_PROMPT
    assert "## Page" in PDF_TRANSCRIPTION_PROMPT, PDF_TRANSCRIPTION_PROMPT


def test_scanned_pdf_marker_exists():
    from services.rag_service import SCANNED_PDF_MARKER
    assert SCANNED_PDF_MARKER.strip(), "marker must be non-empty"
    assert "scanned" in SCANNED_PDF_MARKER.lower()


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
    print("\nRunning PDF fallback tests...\n")

    _run("_pdf_message_content shape + base64 round-trip", test_pdf_message_content_shape_and_roundtrip)
    _run("_pdf_message_content document block is first", test_pdf_message_content_document_block_is_first)
    _run("generate_text_with_pdf rejects explicit OpenAI model", test_generate_text_with_pdf_rejects_openai_model)
    _run("generate_text_with_pdf rejects OpenAI default main_model", test_generate_text_with_pdf_rejects_openai_default_main_model)
    _run("_pdf_needs_vision_fallback: empty chunks -> True", test_needs_fallback_empty_chunks_is_true)
    _run("_pdf_needs_vision_fallback: dense text -> False", test_needs_fallback_dense_text_is_false)
    _run("_pdf_needs_vision_fallback: sparse text -> True", test_needs_fallback_sparse_text_is_true)
    _run("_pdf_needs_vision_fallback: zero page_count doesn't crash", test_needs_fallback_zero_page_count_does_not_crash)
    _run("_pdf_needs_vision_fallback: boundary at threshold", test_needs_fallback_boundary_at_threshold)
    _run("_within_fallback_guards: small/short PDF passes", test_within_fallback_guards_small_short_pdf_passes)
    _run("_within_fallback_guards: oversized file fails", test_within_fallback_guards_oversized_file_fails)
    _run("_within_fallback_guards: too many pages fails", test_within_fallback_guards_too_many_pages_fails)
    _run("PDF_TRANSCRIPTION_PROMPT mentions ## Page", test_transcription_prompt_mentions_page_heading)
    _run("SCANNED_PDF_MARKER exists and mentions scanned", test_scanned_pdf_marker_exists)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
