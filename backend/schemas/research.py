from datetime import datetime
from pydantic import BaseModel, Field


class ResearchStartRequest(BaseModel):
    query: str = Field(max_length=20000)
    max_sources: int = Field(5, ge=1, le=20)
    # Overrides ModelSettings.main_model for this session's synthesis step only
    # (query decomposition and per-source summaries still use the fast model).
    # None falls back to the configured default.
    model: str | None = None


class SearchResultResponse(BaseModel):
    id: str
    url: str
    title: str | None
    snippet: str | None
    ai_summary: str | None
    relevance_score: float | None
    published_date: str | None
    result_order: int

    model_config = {"from_attributes": True}


class ResearchSessionResponse(BaseModel):
    id: str
    query: str
    topic: str | None
    status: str
    summary: str | None
    created_at: datetime
    completed_at: datetime | None
    latest_report_id: str | None = None

    model_config = {"from_attributes": True}


class ResearchSessionDetail(ResearchSessionResponse):
    results: list[SearchResultResponse] = []
