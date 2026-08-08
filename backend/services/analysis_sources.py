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
import re

from sqlalchemy.orm import Session

from models import RiskAnalysis, SearchResult
from services.source_tier import DEFAULT_TIER, TIERS, tier_label
from templates import DIMENSION_REGISTRY


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


# "[Source 9]" and "[Sources 6, 10, 15]" — the two shapes the assessments use.
_CITATION_RE = re.compile(r"\[Sources?\s+([0-9,\s]+)\]")

# Dimension sections are emitted as "### {title}" by _build_dimension_prompt.
_DIMENSION_HEADING_RE = re.compile(r"(?m)^###\s+(.+?)\s*$")

_KEY_BY_TITLE = {dim["title"]: key for key, dim in DIMENSION_REGISTRY.items()}


def dimension_citations(content: str, sources: list[dict]) -> dict[str, dict]:
    """Which sources each risk dimension actually cited, and their provenance.

    Answers the question the grounding score cannot: a dimension can be fully
    grounded in the retrieved material — the text faithfully reports what the
    sources said — while those sources are all vendor marketing. GDPR's
    Compliance Burden dimension is the worked example: grounding 8/10, and the
    text itself says the magnitude "should be treated as unquantified rather
    than established", because two of its six citations are vendor pages.

    Deliberately returns COUNTS PER TIER, not a score. Assigning numbers to
    provenance labels and averaging them would manufacture a precise-looking
    figure from an ordering that has no defensible spacing — the same trap as
    multiplying LLM-estimated ordinals. The distribution is the finding.

    Returns {dimension_key: {"orders": [...], "tiers": {tier: count}}},
    covering only dimensions that appear in the content and cite something.
    """
    if not content or not sources:
        return {}

    tier_by_order = {s["order"]: (s.get("tier") or DEFAULT_TIER) for s in sources}

    out: dict[str, dict] = {}
    headings = list(_DIMENSION_HEADING_RE.finditer(content))
    for i, match in enumerate(headings):
        key = _KEY_BY_TITLE.get(match.group(1).strip())
        if key is None:
            continue  # a heading that isn't a known dimension
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        section = content[match.end():end]

        orders = sorted({
            int(n)
            for group in _CITATION_RE.findall(section)
            for n in re.findall(r"\d+", group)
        })
        # A citation number with no matching source is dropped rather than
        # counted as unknown: it means the model invented a number, and
        # silently tallying it would overstate how much evidence there is.
        orders = [o for o in orders if o in tier_by_order]
        if not orders:
            continue

        tiers: dict[str, int] = {}
        for o in orders:
            t = tier_by_order[o]
            tiers[t] = tiers.get(t, 0) + 1
        out[key] = {
            "orders": orders,
            # Ordered by TIERS so every dimension's breakdown reads the same way.
            "tiers": {t: tiers[t] for t in TIERS if t in tiers},
        }
    return out


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


def format_score_summary_markdown(
    scores: dict, evidence_support: dict, citations: dict,
) -> str:
    """Score, evidence support and citation mix per dimension, for exports.

    Exists because all three already existed and none of them left the app: a
    reviewer reading an exported PDF saw the model's inline "Score: 6/10" and
    nothing else, so the two measures that qualify that number were invisible
    outside the UI.

    The three columns answer three different questions and are deliberately
    never combined:
      Score            — how much this raises the risk
      Evidence support — how well the retrieved material supports what was written
      Sources cited    — who published the material it was written from
    A dimension can be well supported by weak sources, which is precisely the
    case a single blended number would hide.
    """
    if not scores:
        return ""

    rows = []
    for key, score in scores.items():
        dim = DIMENSION_REGISTRY.get(key)
        title = dim["title"] if dim else key
        ev = evidence_support.get(key)
        ev_text = f"{ev}/10" if ev is not None else "not assessed"
        mix = citations.get(key, {}).get("tiers") or {}
        mix_text = ", ".join(f"{n} {tier_label(t).lower()}" for t, n in mix.items()) or "—"
        rows.append(f"- {title} — score {score}/10, evidence support {ev_text}, cites: {mix_text}")

    notes = [
        "Evidence support measures how directly the retrieved source material "
        "supports what was written. It is not a measure of source authority, "
        "and not a probability that the assessment is correct.",
        "Dimensions graded below 6 trigger an extra targeted research pass and "
        "are rewritten if that improves their grounding, so published values "
        "cluster high — the figure reports the result of that process, not the "
        "first attempt.",
        "Scores are conditional on the sources retrieved for this run. A "
        "re-run gathers different sources and may score differently.",
    ]
    body = "\n".join(rows) + "\n\n" + "\n\n".join(notes)
    return f"\n\n---\n\n## Score Summary\n\n{body}"
