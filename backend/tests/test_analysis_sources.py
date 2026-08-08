"""Tests for services/analysis_sources.py — resolving [Source N] citations.

The assessment text cites sources only by number. These tests pin the two
things that make those numbers mean something: the +1 offset between
search_results.result_order and the [Source N] the model writes, and the
fallback that keeps analyses generated before the list was persisted (every
analysis up to 2026-08-07) resolvable from their research session.

Run from the backend directory:
    ./venv/bin/python -m tests.test_analysis_sources
"""
import json
import os
import sys
import uuid

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from models import RiskAnalysis, ResearchSession, SearchResult
from services.analysis_sources import (
    collect_source,
    format_sources_markdown,
    resolve_sources,
)


def _db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _session_with_results(db, n=3, titles=None):
    """A research session with n search results in result_order 0..n-1."""
    session_id = str(uuid.uuid4())
    db.add(ResearchSession(id=session_id, query="q", status="complete"))
    for i in range(n):
        db.add(SearchResult(
            id=str(uuid.uuid4()), session_id=session_id,
            url=f"https://example.com/{i}",
            title=(titles[i] if titles else f"Result {i}"),
            result_order=i,
        ))
    db.commit()
    return session_id


def _analysis(db, session_id=None, sources_json=None):
    analysis_id = str(uuid.uuid4())
    db.add(RiskAnalysis(
        id=analysis_id, subject="Subject", analysis_type="policy",
        session_id=session_id, sources_json=sources_json,
    ))
    db.commit()
    return db.query(RiskAnalysis).filter(RiskAnalysis.id == analysis_id).first()


# ── The +1 offset: result_order 0 is cited as [Source 1] ─────────────────────

def test_order_is_one_based_not_zero_based():
    """research_agent renders sources as `[Source {order}]` where order is
    result_order + 1, so a 0-based result_order must surface as 1. Getting
    this off by one would silently attribute every claim to the wrong source."""
    db = _db()
    sid = _session_with_results(db, n=3)
    sources = resolve_sources(db, _analysis(db, session_id=sid))
    assert [s["order"] for s in sources] == [1, 2, 3], sources
    db.close()


def test_sources_returned_in_citation_order():
    db = _db()
    sid = _session_with_results(db, n=5)
    sources = resolve_sources(db, _analysis(db, session_id=sid))
    assert [s["order"] for s in sources] == sorted(s["order"] for s in sources)
    assert [s["url"] for s in sources] == [f"https://example.com/{i}" for i in range(5)]
    db.close()


def test_collect_source_matches_resolve_shape():
    """The persist path (risk_analyzer) and the fallback path (resolve_sources)
    must produce identical dicts, or an analysis would render differently
    before and after its session rows are gone."""
    db = _db()
    sid = _session_with_results(db, n=2)
    rows = db.query(SearchResult).order_by(SearchResult.result_order).all()
    assert [collect_source(r) for r in rows] == resolve_sources(db, _analysis(db, session_id=sid))
    db.close()


def test_title_falls_back_to_url_when_missing():
    db = _db()
    session_id = str(uuid.uuid4())
    db.add(ResearchSession(id=session_id, query="q", status="complete"))
    db.add(SearchResult(
        id=str(uuid.uuid4()), session_id=session_id,
        url="https://example.com/untitled", title=None, result_order=0,
    ))
    db.commit()
    sources = resolve_sources(db, _analysis(db, session_id=session_id))
    assert sources[0]["title"] == "https://example.com/untitled", sources
    db.close()


# ── Persisted list wins; session rows are the fallback ───────────────────────

def test_persisted_sources_preferred_over_session_rows():
    """Once stored on the analysis, the list is authoritative — an export must
    not silently change if the session's rows are edited or re-ordered."""
    db = _db()
    sid = _session_with_results(db, n=3)
    stored = [{"order": 1, "title": "Stored", "url": "https://stored.example"}]
    sources = resolve_sources(db, _analysis(db, session_id=sid, sources_json=json.dumps(stored)))
    assert sources == stored, sources
    db.close()


def test_falls_back_to_session_when_not_persisted():
    """The backward-compatibility path: analyses created before the list was
    persisted still resolve from their research session."""
    db = _db()
    sid = _session_with_results(db, n=4)
    assert len(resolve_sources(db, _analysis(db, session_id=sid))) == 4
    db.close()


def test_corrupt_persisted_json_falls_back_rather_than_raising():
    db = _db()
    sid = _session_with_results(db, n=2)
    sources = resolve_sources(db, _analysis(db, session_id=sid, sources_json="{not json"))
    assert len(sources) == 2, sources
    db.close()


def test_empty_persisted_list_falls_back():
    """`sources_json` of "[]" is what a failed persist looks like; prefer the
    session rows over rendering no citations at all."""
    db = _db()
    sid = _session_with_results(db, n=2)
    assert len(resolve_sources(db, _analysis(db, session_id=sid, sources_json="[]"))) == 2
    db.close()


def test_no_session_and_no_persisted_returns_empty():
    db = _db()
    assert resolve_sources(db, _analysis(db)) == []
    db.close()


# ── Export rendering ─────────────────────────────────────────────────────────

def test_markdown_renders_citation_keys_not_a_list():
    """The number is the citation key. Rendered literally as [Source N] rather
    than as an ordered list, which a renderer could renumber. Each entry also
    carries its publisher provenance, so an exported document says which
    citations came from a vendor and which from a regulator."""
    md = format_sources_markdown([
        {"order": 1, "title": "First", "url": "https://a.example", "tier": "official"},
        {"order": 2, "title": "Second", "url": "https://b.example", "tier": "vendor"},
    ])
    assert "## Sources" in md
    assert "[Source 1] First (Official / Regulator) — https://a.example" in md
    assert "[Source 2] Second (Vendor / Commercial) — https://b.example" in md


def test_markdown_labels_untiered_sources_as_unclassified():
    """Sources gathered before tiering existed have no tier; they must still
    render, labelled unclassified rather than silently as a low tier."""
    md = format_sources_markdown([{"order": 1, "title": "Old", "url": "https://a.example"}])
    assert "[Source 1] Old (Unclassified) — https://a.example" in md


def test_markdown_empty_when_no_sources():
    """No sources must render nothing at all — not a bare 'Sources' heading."""
    assert format_sources_markdown([]) == ""


# ── Test runner ───────────────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run_test(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")


if __name__ == "__main__":
    print("\nRunning analysis source-resolution tests...\n")

    _run_test("order is 1-based, not 0-based", test_order_is_one_based_not_zero_based)
    _run_test("sources returned in citation order", test_sources_returned_in_citation_order)
    _run_test("collect_source matches resolve shape", test_collect_source_matches_resolve_shape)
    _run_test("title falls back to url", test_title_falls_back_to_url_when_missing)
    _run_test("persisted sources preferred", test_persisted_sources_preferred_over_session_rows)
    _run_test("falls back to session rows", test_falls_back_to_session_when_not_persisted)
    _run_test("corrupt persisted JSON falls back", test_corrupt_persisted_json_falls_back_rather_than_raising)
    _run_test("empty persisted list falls back", test_empty_persisted_list_falls_back)
    _run_test("no session and none persisted → empty", test_no_session_and_no_persisted_returns_empty)
    _run_test("markdown renders citation keys", test_markdown_renders_citation_keys_not_a_list)
    _run_test("markdown labels untiered as unclassified", test_markdown_labels_untiered_sources_as_unclassified)
    _run_test("markdown empty when no sources", test_markdown_empty_when_no_sources)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print("Failed: " + ", ".join(_FAILED))
        sys.exit(1)
    print("All tests passed.")
