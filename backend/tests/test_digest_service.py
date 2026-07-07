"""Tests for the digest email headline language and HTML rendering.

The digest historically generated Japanese headlines; per the project rule
"App UI must always be in English" the headline prompt and the email markup
are now English. These tests pin that down without any network calls
(generate_text is monkeypatched).

Run from the backend directory:
    ./venv/bin/python -m tests.test_digest_service
"""
import asyncio
import os
import re
import sys

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import services.digest_service as digest_service
from services.regulatory_tracker import RegulatoryDocument
from services.tavily_client import SearchResult


def _article(title="AI Act enters into force", snippet="The EU AI Act...") -> SearchResult:
    return SearchResult(
        url="https://example.com/a",
        title=title,
        snippet=snippet,
        content=None,
        score=0.9,
        published_date=None,
    )


def _reg_doc(
    title="AI Risk Management Framework Update",
    abstract="This rule updates the framework...",
    agencies=None,
) -> RegulatoryDocument:
    return RegulatoryDocument(
        document_number="2026-13674",
        title=title,
        abstract=abstract,
        html_url="https://www.federalregister.gov/documents/2026-13674",
        publication_date="2026-07-06",
        document_type="Rule",
        agencies=agencies if agencies is not None else ["Commerce Department"],
    )


_JAPANESE_RE = re.compile(r"[぀-ヿ一-鿿]")  # kana + CJK ideographs


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_headline_prompt_is_english():
    """The prompt sent to Claude asks for English and contains no Japanese."""
    captured: dict = {}

    async def fake_generate_text(prompt, **kwargs):
        captured["prompt"] = prompt
        return "A concise English headline."

    original = digest_service.generate_text
    digest_service.generate_text = fake_generate_text
    try:
        result = asyncio.run(digest_service._generate_headline(_article()))
    finally:
        digest_service.generate_text = original

    assert result == "A concise English headline.", result
    prompt = captured["prompt"]
    assert "English" in prompt, prompt
    assert not _JAPANESE_RE.search(prompt), f"prompt contains Japanese: {prompt!r}"
    assert "AI Act enters into force" in prompt, "article title must be embedded"


def test_headline_falls_back_to_snippet_on_error():
    async def broken_generate_text(prompt, **kwargs):
        raise RuntimeError("api down")

    original = digest_service.generate_text
    digest_service.generate_text = broken_generate_text
    try:
        result = asyncio.run(digest_service._generate_headline(_article(snippet="S" * 300)))
    finally:
        digest_service.generate_text = original

    assert result == "S" * 200, f"expected 200-char snippet fallback, got {len(result)} chars"


def test_html_is_english_and_escaped():
    """Email body declares lang='en', has no Japanese, and escapes article HTML."""
    article = _article(title='<script>alert("x")</script>')
    html_body = digest_service._build_html([(article, "Headline & summary")], "July 1, 2026")

    assert 'lang="en"' in html_body
    assert not _JAPANESE_RE.search(html_body), "email HTML contains Japanese text"
    assert "<script>" not in html_body, "article title must be HTML-escaped"
    assert "&lt;script&gt;" in html_body
    assert "Headline &amp; summary" in html_body


def test_regulatory_section_renders_when_reg_docs_present():
    """The 'Regulatory & Legislative Updates' section appears with title/type/
    agency/date/abstract, and is omitted entirely when reg_docs is empty."""
    article = _article()
    reg_doc = _reg_doc()

    html_with_docs = digest_service._build_html(
        [(article, "Headline")], "July 6, 2026", [reg_doc]
    )
    assert "Regulatory &amp; Legislative Updates" in html_with_docs
    assert "AI Risk Management Framework Update" in html_with_docs
    assert "Commerce Department" in html_with_docs
    assert "Rule" in html_with_docs
    assert "2026-07-06" in html_with_docs
    assert "This rule updates the framework" in html_with_docs
    assert "federalregister.gov/documents/2026-13674" in html_with_docs

    html_without_docs = digest_service._build_html(
        [(article, "Headline")], "July 6, 2026", []
    )
    assert "Regulatory &amp; Legislative Updates" not in html_without_docs

    html_default = digest_service._build_html([(article, "Headline")], "July 6, 2026")
    assert "Regulatory &amp; Legislative Updates" not in html_default


def test_regulatory_section_escapes_external_fields():
    """Title/abstract/agency strings from RegulatoryDocument are HTML-escaped."""
    malicious_doc = _reg_doc(
        title='<script>alert("t")</script>',
        abstract='<img src=x onerror=alert("a")>',
        agencies=['<b>Evil Agency</b>'],
    )
    html_body = digest_service._build_html(
        [(_article(), "Headline")], "July 6, 2026", [malicious_doc]
    )

    assert "<script>" not in html_body, "reg doc title must be HTML-escaped"
    assert "&lt;script&gt;" in html_body
    assert "<img src=x" not in html_body, "reg doc abstract must be HTML-escaped"
    assert "&lt;img src=x" in html_body
    assert "<b>Evil Agency</b>" not in html_body, "reg doc agency must be HTML-escaped"
    assert "&lt;b&gt;Evil Agency&lt;/b&gt;" in html_body


def test_regulatory_abstract_is_truncated_to_300_chars():
    long_abstract = "A" * 500
    reg_doc = _reg_doc(abstract=long_abstract)
    html_body = digest_service._build_html(
        [(_article(), "Headline")], "July 6, 2026", [reg_doc]
    )
    assert "A" * 301 not in html_body, "abstract should be truncated to ~300 chars"
    assert "A" * 300 in html_body


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
    print("\nRunning digest service tests...\n")

    _run("headline prompt is English", test_headline_prompt_is_english)
    _run("headline falls back to snippet on error", test_headline_falls_back_to_snippet_on_error)
    _run("email HTML is English and escaped", test_html_is_english_and_escaped)
    _run("regulatory section renders when present, omitted when empty", test_regulatory_section_renders_when_reg_docs_present)
    _run("regulatory section escapes external fields", test_regulatory_section_escapes_external_fields)
    _run("regulatory abstract is truncated to 300 chars", test_regulatory_abstract_is_truncated_to_300_chars)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
