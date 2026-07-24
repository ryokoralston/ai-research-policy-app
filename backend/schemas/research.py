from datetime import datetime
from pydantic import BaseModel


class ResearchStartRequest(BaseModel):
    query: str
    depth: str = "quick"  # "quick" | "deep"
    max_sources: int = 5


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
