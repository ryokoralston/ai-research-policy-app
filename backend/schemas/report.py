from datetime import datetime
from pydantic import BaseModel


class ReportGenerateRequest(BaseModel):
    report_type: str  # 'congressional_brief' | 'policy_memo' | 'risk_assessment'
    title: str
    session_id: str | None = None
    debate_id: str | None = None
    doc_ids: list[str] | None = None
    custom_instructions: str | None = None
    audience: str = "Congressional staff"


class ReportSectionResponse(BaseModel):
    id: str
    section_key: str
    title: str
    content: str
    order_index: int
    citations_json: str | None

    model_config = {"from_attributes": True}


class ReportResponse(BaseModel):
    id: str
    title: str
    report_type: str
    status: str
    word_count: int | None
    session_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReportDetail(ReportResponse):
    content: str | None
    sections: list[ReportSectionResponse] = []


class ReportUpdateRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    status: str | None = None


class ReportDraftRequest(BaseModel):
    title: str
    report_type: str
