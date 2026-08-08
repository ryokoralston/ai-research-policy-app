from datetime import datetime
from pydantic import BaseModel


class AnalysisStartRequest(BaseModel):
    subject: str
    # Free-form label describing what kind of thing is being assessed; it is
    # injected verbatim into the analysis prompt, so it is deliberately not an
    # enum. The frontend offers: 'technology' | 'policy' | 'actor' |
    # 'use_case' | 'supply_chain'.
    analysis_type: str = "technology"
    context: str | None = None
    run_web_research: bool = True


class SourceRef(BaseModel):
    """One resolved [Source N] citation. `order` is the number as it appears
    in the assessment text."""
    order: int
    title: str
    url: str
    # Publisher provenance, one of services/source_tier.py's TIERS. Defaults to
    # "unknown" for sources gathered before tiering existed.
    tier: str = "unknown"


class RiskAnalysisResponse(BaseModel):
    id: str
    subject: str
    analysis_type: str
    content: str | None
    risk_scores_json: str | None
    citation_confidence_json: str | None
    dimension_confidence_json: str | None
    sources_json: str | None
    session_id: str | None
    created_at: datetime
    # Resolved citation list. Populated only by the detail endpoint — the list
    # endpoint leaves it empty rather than running a source lookup per row.
    sources: list[SourceRef] = []

    model_config = {"from_attributes": True}
