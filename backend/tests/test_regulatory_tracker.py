"""Tests for the Federal Register regulatory tracker client.

Covers: query param construction, response parsing into RegulatoryDocument
(including missing abstract / empty agencies), fetch_top_regulatory_documents's
dedup-by-document_number and sort-by-date-descending behavior, and that a
per-topic exception doesn't kill the whole batch.

No real network calls — httpx.AsyncClient.get is monkeypatched.

Run from the backend directory:
    ./venv/bin/python -m tests.test_regulatory_tracker
"""
import asyncio
import os
import sys

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import httpx

import services.regulatory_tracker as regulatory_tracker
from services.regulatory_tracker import (
    FederalRegisterClient,
    RegulatoryDocument,
    fetch_top_regulatory_documents,
)


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, json_data):
        self._json_data = json_data

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


class _FakeAsyncClient:
    """Captures the last GET call's url/params and returns canned JSON."""
    last_params = None
    last_url = None
    json_to_return = {"results": []}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        _FakeAsyncClient.last_url = url
        _FakeAsyncClient.last_params = params
        return _FakeResponse(_FakeAsyncClient.json_to_return)


def _sample_result(
    document_number="2026-13674",
    title="Some Rule",
    abstract="An abstract.",
    publication_date="2026-07-07",
    doc_type="Rule",
    agencies=None,
):
    if agencies is None:
        agencies = [{"name": "Energy Department"}]
    return {
        "document_number": document_number,
        "title": title,
        "abstract": abstract,
        "html_url": f"https://www.federalregister.gov/documents/{document_number}",
        "publication_date": publication_date,
        "type": doc_type,
        "agencies": agencies,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_query_params_include_term_per_page_and_order():
    """search() builds the expected query params, without publication_date if omitted."""
    original = httpx.AsyncClient
    httpx.AsyncClient = _FakeAsyncClient
    _FakeAsyncClient.json_to_return = {"results": [_sample_result()]}
    try:
        client = FederalRegisterClient()
        asyncio.run(client.search("artificial intelligence", max_results=7))
    finally:
        httpx.AsyncClient = original

    params = _FakeAsyncClient.last_params
    assert params["conditions[term]"] == "artificial intelligence", params
    assert params["per_page"] == 7, params
    assert params["order"] == "newest", params
    assert "conditions[publication_date][gte]" not in params, params
    assert _FakeAsyncClient.last_url == f"{FederalRegisterClient.BASE_URL}/documents.json"


def test_query_params_include_published_after_when_given():
    original = httpx.AsyncClient
    httpx.AsyncClient = _FakeAsyncClient
    _FakeAsyncClient.json_to_return = {"results": []}
    try:
        client = FederalRegisterClient()
        asyncio.run(client.search("AI policy", published_after="2026-07-06", max_results=5))
    finally:
        httpx.AsyncClient = original

    params = _FakeAsyncClient.last_params
    assert params["conditions[publication_date][gte]"] == "2026-07-06", params


def test_parses_response_into_regulatory_documents():
    original = httpx.AsyncClient
    httpx.AsyncClient = _FakeAsyncClient
    _FakeAsyncClient.json_to_return = {"results": [_sample_result()]}
    try:
        client = FederalRegisterClient()
        docs = asyncio.run(client.search("artificial intelligence"))
    finally:
        httpx.AsyncClient = original

    assert len(docs) == 1
    doc = docs[0]
    assert isinstance(doc, RegulatoryDocument)
    assert doc.document_number == "2026-13674"
    assert doc.title == "Some Rule"
    assert doc.abstract == "An abstract."
    assert doc.document_type == "Rule"
    assert doc.agencies == ["Energy Department"]
    assert doc.publication_date == "2026-07-07"


def test_parses_document_missing_abstract_and_empty_agencies():
    result = _sample_result(document_number="2026-00001", abstract=None, agencies=[])
    original = httpx.AsyncClient
    httpx.AsyncClient = _FakeAsyncClient
    _FakeAsyncClient.json_to_return = {"results": [result]}
    try:
        client = FederalRegisterClient()
        docs = asyncio.run(client.search("AI"))
    finally:
        httpx.AsyncClient = original

    assert len(docs) == 1
    doc = docs[0]
    assert doc.abstract is None
    assert doc.agencies == []


def test_fetch_top_regulatory_documents_dedups_and_sorts_by_date_desc():
    """Two topics return overlapping docs; result is deduped by document_number
    and sorted newest-first."""
    doc_a = RegulatoryDocument(
        document_number="A", title="Doc A", abstract="x",
        html_url="https://x/a", publication_date="2026-07-01",
        document_type="Rule", agencies=["Agency 1"],
    )
    doc_b = RegulatoryDocument(
        document_number="B", title="Doc B", abstract="x",
        html_url="https://x/b", publication_date="2026-07-05",
        document_type="Notice", agencies=["Agency 2"],
    )
    # doc_a duplicated across both topics — should only appear once in output.
    doc_a_dup = RegulatoryDocument(
        document_number="A", title="Doc A (dup)", abstract="x",
        html_url="https://x/a", publication_date="2026-07-01",
        document_type="Rule", agencies=["Agency 1"],
    )

    calls = []

    async def fake_search(self, query, published_after=None, max_results=5):
        calls.append(query)
        if query == "topic1":
            return [doc_a, doc_b]
        if query == "topic2":
            return [doc_a_dup]
        return []

    original = FederalRegisterClient.search
    FederalRegisterClient.search = fake_search
    try:
        docs = asyncio.run(fetch_top_regulatory_documents(["topic1", "topic2"], max_total=5))
    finally:
        FederalRegisterClient.search = original

    assert calls == ["topic1", "topic2"]
    assert len(docs) == 2, f"expected dedup to 2 docs, got {len(docs)}: {docs}"
    assert [d.document_number for d in docs] == ["B", "A"], "expected newest-first sort"


def test_fetch_top_regulatory_documents_survives_per_topic_exception():
    """One topic raising an exception must not prevent results from other topics."""
    good_doc = RegulatoryDocument(
        document_number="OK", title="Fine", abstract=None,
        html_url="https://x/ok", publication_date="2026-07-03",
        document_type="Rule", agencies=[],
    )

    async def flaky_search(self, query, published_after=None, max_results=5):
        if query == "broken-topic":
            raise RuntimeError("Federal Register API is down")
        return [good_doc]

    original = FederalRegisterClient.search
    FederalRegisterClient.search = flaky_search
    try:
        docs = asyncio.run(
            fetch_top_regulatory_documents(["broken-topic", "good-topic"], max_total=5)
        )
    finally:
        FederalRegisterClient.search = original

    assert len(docs) == 1, f"expected the good topic's result to survive, got {docs}"
    assert docs[0].document_number == "OK"


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
    print("\nRunning regulatory tracker tests...\n")

    _run("query params include term/per_page/order", test_query_params_include_term_per_page_and_order)
    _run("query params include published_after when given", test_query_params_include_published_after_when_given)
    _run("parses response into RegulatoryDocument objects", test_parses_response_into_regulatory_documents)
    _run("parses doc missing abstract and empty agencies", test_parses_document_missing_abstract_and_empty_agencies)
    _run("fetch_top dedups and sorts by date desc", test_fetch_top_regulatory_documents_dedups_and_sorts_by_date_desc)
    _run("fetch_top survives per-topic exception", test_fetch_top_regulatory_documents_survives_per_topic_exception)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
