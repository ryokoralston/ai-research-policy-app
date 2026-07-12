"""Tests for the single-document cited Q&A module (services/document_qa.py):
the pure block-construction helpers (_pdf_document_block/_text_document_block),
the size/page/scanned-marker guard logic that picks between the native-PDF and
plain-text paths (build_document_block, _pdf_within_ask_guards), and the pure
citation-event mapping (_citation_payload) used by the SSE streaming dispatch.

No live API calls, no real DB — build_document_block's chunk fetch is
monkeypatched (_ordered_chunk_text is document_qa's single injectable seam
for that) and PDF-guard tests use real tmp files on disk.

Run from the backend directory:
    ./venv/bin/python -m tests.test_document_qa
"""
import base64
import os
import sys
import tempfile
import types

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

import services.document_qa as document_qa
from services.rag_service import SCANNED_PDF_MARKER


# ── _pdf_document_block ───────────────────────────────────────────────────────

def test_pdf_document_block_shape_and_roundtrip():
    pdf_bytes = b"%PDF-1.4\nfake-pdf-bytes-for-testing"
    block = document_qa._pdf_document_block(pdf_bytes, "My Document")

    assert block["type"] == "document", block
    assert block["source"]["type"] == "base64", block
    assert block["source"]["media_type"] == "application/pdf", block
    assert block["citations"] == {"enabled": True}, block
    assert block["title"] == "My Document", block
    assert block["cache_control"] == {"type": "ephemeral"}, block

    decoded = base64.standard_b64decode(block["source"]["data"])
    assert decoded == pdf_bytes, decoded


def test_pdf_document_block_title_propagation():
    block = document_qa._pdf_document_block(b"bytes", "Custom Title")
    assert block["title"] == "Custom Title"


# ── _text_document_block ──────────────────────────────────────────────────────

def test_text_document_block_shape():
    block = document_qa._text_document_block("some document text", "Text Doc")

    assert block["type"] == "document", block
    assert block["source"] == {
        "type": "text",
        "media_type": "text/plain",
        "data": "some document text",
    }, block
    assert block["citations"] == {"enabled": True}, block
    assert block["title"] == "Text Doc", block
    assert block["cache_control"] == {"type": "ephemeral"}, block


def test_text_document_block_title_propagation():
    block = document_qa._text_document_block("text", "Another Title")
    assert block["title"] == "Another Title"


# ── _pdf_within_ask_guards ─────────────────────────────────────────────────────

def _fake_doc(file_path, page_count):
    return types.SimpleNamespace(file_path=file_path, page_count=page_count)


def test_pdf_within_ask_guards_missing_file_fails():
    doc = _fake_doc("/nonexistent/path/does-not-exist.pdf", 5)
    assert document_qa._pdf_within_ask_guards(doc) is False


def test_pdf_within_ask_guards_no_file_path_fails():
    doc = _fake_doc(None, 5)
    assert document_qa._pdf_within_ask_guards(doc) is False


def test_pdf_within_ask_guards_small_short_pdf_passes():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4\nsmall fake pdf")
        path = f.name
    try:
        doc = _fake_doc(path, 5)
        assert document_qa._pdf_within_ask_guards(doc) is True
    finally:
        os.remove(path)


def test_pdf_within_ask_guards_oversized_file_fails():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"x" * 1024)
        path = f.name
    try:
        doc = _fake_doc(path, 5)
        original_max = document_qa.MAX_PDF_ASK_BYTES
        document_qa.MAX_PDF_ASK_BYTES = 100  # shrink the guard below the file size
        try:
            assert document_qa._pdf_within_ask_guards(doc) is False
        finally:
            document_qa.MAX_PDF_ASK_BYTES = original_max
    finally:
        os.remove(path)


def test_pdf_within_ask_guards_too_many_pages_fails():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4\nfake")
        path = f.name
    try:
        doc = _fake_doc(path, document_qa.MAX_PDF_ASK_PAGES + 1)
        assert document_qa._pdf_within_ask_guards(doc) is False
    finally:
        os.remove(path)


def test_pdf_within_ask_guards_boundary_page_count_passes():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4\nfake")
        path = f.name
    try:
        doc = _fake_doc(path, document_qa.MAX_PDF_ASK_PAGES)
        assert document_qa._pdf_within_ask_guards(doc) is True
    finally:
        os.remove(path)


# ── build_document_block ───────────────────────────────────────────────────────
# _ordered_chunk_text is document_qa's one DB-touching seam; monkeypatch it so
# these tests never construct a real Session.

def _with_patched_chunk_text(text, fn):
    original = document_qa._ordered_chunk_text
    document_qa._ordered_chunk_text = lambda doc_id, db: text
    try:
        return fn()
    finally:
        document_qa._ordered_chunk_text = original


def test_build_document_block_native_pdf_within_guards():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4\nreal pdf content")
        path = f.name
    try:
        doc = types.SimpleNamespace(
            file_path=path, page_count=3, title="Report", filename="report.pdf", id="doc-1",
        )
        block, source_kind = _with_patched_chunk_text(
            "extracted chunk text, not a scanned marker",
            lambda: document_qa.build_document_block(doc, db=None),
        )
        assert source_kind == "pdf", source_kind
        assert block["source"]["type"] == "base64", block
        assert block["title"] == "Report", block
    finally:
        os.remove(path)


def test_build_document_block_oversized_pdf_falls_back_to_text():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4\nfake")
        path = f.name
    try:
        doc = types.SimpleNamespace(
            file_path=path, page_count=document_qa.MAX_PDF_ASK_PAGES + 1,
            title=None, filename="huge.pdf", id="doc-2",
        )
        block, source_kind = _with_patched_chunk_text(
            "some chunked text content",
            lambda: document_qa.build_document_block(doc, db=None),
        )
        assert source_kind == "text", source_kind
        assert block["source"]["type"] == "text", block
        assert block["source"]["data"] == "some chunked text content", block
        assert block["title"] == "huge.pdf", block  # falls back to filename when title is None
    finally:
        os.remove(path)


def test_build_document_block_missing_file_falls_back_to_text():
    doc = types.SimpleNamespace(
        file_path="/nonexistent/missing.pdf", page_count=3,
        title="Missing", filename="missing.pdf", id="doc-3",
    )
    block, source_kind = _with_patched_chunk_text(
        "chunk text for a doc whose file vanished",
        lambda: document_qa.build_document_block(doc, db=None),
    )
    assert source_kind == "text", source_kind
    assert block["source"]["type"] == "text", block


def test_build_document_block_scanned_pdf_falls_back_to_text():
    """A PDF within size/page guards but whose extracted text carries
    SCANNED_PDF_MARKER (vision-transcribed at index time) must use the
    stored transcription rather than re-sending the raw scanned bytes."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4\nscanned pdf raw bytes")
        path = f.name
    try:
        doc = types.SimpleNamespace(
            file_path=path, page_count=2, title="Scan", filename="scan.pdf", id="doc-4",
        )
        transcribed = f"{SCANNED_PDF_MARKER}\nPage 1 transcribed text"
        block, source_kind = _with_patched_chunk_text(
            transcribed,
            lambda: document_qa.build_document_block(doc, db=None),
        )
        assert source_kind == "text", source_kind
        assert block["source"]["data"] == transcribed, block
    finally:
        os.remove(path)


def test_build_document_block_txt_file_uses_text_path():
    doc = types.SimpleNamespace(
        file_path="/some/path/doc.txt", page_count=None,
        title="Plain Text Doc", filename="doc.txt", id="doc-5",
    )
    block, source_kind = _with_patched_chunk_text(
        "plain text file content",
        lambda: document_qa.build_document_block(doc, db=None),
    )
    assert source_kind == "text", source_kind
    assert block["source"]["data"] == "plain text file content", block


# ── _citation_payload ──────────────────────────────────────────────────────────

def test_citation_payload_pdf_page_located():
    citation = types.SimpleNamespace(
        type="page_location",
        cited_text="the exact quoted passage",
        document_title="Policy Report",
        start_page_number=3,
        end_page_number=4,
    )
    payload = document_qa._citation_payload(citation, 1, "pdf")

    assert payload["index"] == 1
    assert payload["cited_text"] == "the exact quoted passage"
    assert payload["document_title"] == "Policy Report"
    assert payload["source_kind"] == "pdf"
    assert payload["start_page_number"] == 3
    assert payload["end_page_number"] == 4
    assert "start_char_index" not in payload
    assert "end_char_index" not in payload


def test_citation_payload_text_char_located():
    citation = types.SimpleNamespace(
        type="char_location",
        cited_text="a quoted span from plain text",
        document_title="Scraped Article",
        start_char_index=120,
        end_char_index=150,
    )
    payload = document_qa._citation_payload(citation, 2, "text")

    assert payload["index"] == 2
    assert payload["source_kind"] == "text"
    assert payload["start_char_index"] == 120
    assert payload["end_char_index"] == 150
    assert "start_page_number" not in payload
    assert "end_page_number" not in payload


def test_citation_payload_trims_long_cited_text():
    long_text = "x" * 500
    citation = types.SimpleNamespace(
        cited_text=long_text, document_title="Doc",
        start_page_number=1, end_page_number=1,
    )
    payload = document_qa._citation_payload(citation, 1, "pdf")

    assert len(payload["cited_text"]) <= document_qa.MAX_CITED_TEXT_CHARS + 1  # +1 for the ellipsis char
    assert payload["cited_text"].endswith("…")


def test_citation_payload_short_cited_text_not_trimmed():
    citation = types.SimpleNamespace(
        cited_text="short quote", document_title="Doc",
        start_page_number=1, end_page_number=1,
    )
    payload = document_qa._citation_payload(citation, 1, "pdf")
    assert payload["cited_text"] == "short quote"


def test_citation_payload_accepts_plain_dict():
    """_citation_payload must work with either an SDK object (attribute
    access) or a plain dict (key access) — same dual-access contract as
    anthropic_client._block_get, which it reuses."""
    citation = {
        "cited_text": "dict-based citation",
        "document_title": "Dict Doc",
        "start_page_number": 5,
        "end_page_number": 5,
    }
    payload = document_qa._citation_payload(citation, 3, "pdf")
    assert payload["cited_text"] == "dict-based citation"
    assert payload["start_page_number"] == 5


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
    print("\nRunning document_qa tests...\n")

    _run("_pdf_document_block shape + base64 round-trip", test_pdf_document_block_shape_and_roundtrip)
    _run("_pdf_document_block title propagation", test_pdf_document_block_title_propagation)
    _run("_text_document_block shape", test_text_document_block_shape)
    _run("_text_document_block title propagation", test_text_document_block_title_propagation)

    _run("_pdf_within_ask_guards: missing file fails", test_pdf_within_ask_guards_missing_file_fails)
    _run("_pdf_within_ask_guards: no file_path fails", test_pdf_within_ask_guards_no_file_path_fails)
    _run("_pdf_within_ask_guards: small/short PDF passes", test_pdf_within_ask_guards_small_short_pdf_passes)
    _run("_pdf_within_ask_guards: oversized file fails", test_pdf_within_ask_guards_oversized_file_fails)
    _run("_pdf_within_ask_guards: too many pages fails", test_pdf_within_ask_guards_too_many_pages_fails)
    _run("_pdf_within_ask_guards: boundary page count passes", test_pdf_within_ask_guards_boundary_page_count_passes)

    _run("build_document_block: native PDF within guards", test_build_document_block_native_pdf_within_guards)
    _run("build_document_block: oversized PDF falls back to text", test_build_document_block_oversized_pdf_falls_back_to_text)
    _run("build_document_block: missing file falls back to text", test_build_document_block_missing_file_falls_back_to_text)
    _run("build_document_block: scanned PDF falls back to text", test_build_document_block_scanned_pdf_falls_back_to_text)
    _run("build_document_block: txt file uses text path", test_build_document_block_txt_file_uses_text_path)

    _run("_citation_payload: PDF page-located", test_citation_payload_pdf_page_located)
    _run("_citation_payload: text char-located", test_citation_payload_text_char_located)
    _run("_citation_payload: trims long cited_text", test_citation_payload_trims_long_cited_text)
    _run("_citation_payload: short cited_text not trimmed", test_citation_payload_short_cited_text_not_trimmed)
    _run("_citation_payload: accepts plain dict", test_citation_payload_accepts_plain_dict)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
