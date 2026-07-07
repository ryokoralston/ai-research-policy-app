from datetime import datetime
from pydantic import BaseModel


class AnalysisStartRequest(BaseModel):
    subject: str
    analysis_type: str = "technology"  # 'technology' | 'policy' | 'actor'
    context: str | None = None
    run_web_research: bool = True


class RiskAnalysisResponse(BaseModel):
    id: str
    subject: str
    analysis_type: str
    content: str | None
    risk_scores_json: str | None
    citation_confidence_json: str | None
    sources_json: str | None
    session_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
