"""Resolve the [Source N] markers in a risk analysis back to real citations.

run_risk_analysis feeds the research synthesis into the assessment prompt, and
that synthesis numbers its sources as "[Source 1] Title (url)" — see
research_agent.build_synthesis_prompt, which renders `order` where `order` is
`result_order + 1`. The model then cites [Source N] throughout the assessment.

Nothing used to map those markers back: the analysis stored no source list, and
the exports rendered `content` alone, so a finished report cited seventeen
sources a reader had no way to identify. This module is that mapping.

`order` here is the 1-based number as it appears in the text, i.e.
`search_results.result_order + 1` — the same +1 the synthesis prompt applies.

Note: the extra Tavily searches run by risk_analyzer._fix_weak_dimensions are
deliberately NOT included. Those results are handed to the revision prompt as
raw text and never enter the [Source N] numbering, so listing them here would
introduce citation numbers that appear nowhere in the document.
"""
import json

from sqlalchemy.orm import Session

from models import RiskAnalysis, SearchResult
from services.source_tier import DEFAULT_TIER, tier_label


def resolve_sources(db: Session, analysis: RiskAnalysis) -> list[dict]:
    """Return [{order, title, url}, ...] for an analysis, in citation order.

    Prefers the list persisted on the analysis itself, so an export stays
    correct even if the research session is later deleted. Falls back to the
    session's search_results, which is what makes analyses generated before
    this list was persisted (every analysis up to 2026-08-07) resolvable too.

    Returns [] when neither source is available — callers render nothing
    rather than an empty "Sources" heading.
    """
    if analysis.sources_json:
        try:
            stored = json.loads(analysis.sources_json)
            if isinstance(stored, list) and stored:
                return stored
        except (ValueError, TypeError):
            # Corrupt JSON shouldn't cost the reader the citation list when
            # the session rows can still supply it — fall through.
            pass

    if not analysis.session_id:
        return []

    rows = (
        db.query(SearchResult)
        .filter(SearchResult.session_id == analysis.session_id)
        .order_by(SearchResult.result_order)
        .all()
    )
    return [collect_source(r) for r in rows]


def collect_source(result: SearchResult) -> dict:
    """One search result as the citation shape stored and rendered.

    Kept as a named helper so the persist path in risk_analyzer and the
    fallback path above cannot drift into producing different shapes.
    """
    return {
        "order": result.result_order + 1,
        "title": result.title or result.url,
        "url": result.url,
        # NULL for sources gathered before tiering existed; renders as
        # unclassified rather than as a low tier.
        "tier": result.source_tier or DEFAULT_TIER,
    }


def format_sources_markdown(sources: list[dict]) -> str:
    """Numbered citation list appended to exported content.

    Numbered explicitly rather than with a markdown ordered list, because the
    number IS the citation key — a renderer that renumbered "1." to "1." would
    silently break the link between [Source 4] and its entry.
    """
    if not sources:
        return ""
    lines = "\n".join(
        f"[Source {s['order']}] {s['title']} ({tier_label(s.get('tier'))}) — {s['url']}"
        for s in sources
    )
    return f"\n\n---\n\n## Sources\n\n{lines}"
