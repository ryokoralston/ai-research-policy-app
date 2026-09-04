from datetime import datetime
from pydantic import BaseModel, Field


class ReportGenerateRequest(BaseModel):
    report_type: str = Field(max_length=100)  # 'congressional_brief' | 'policy_memo' | 'risk_assessment'
    title: str = Field(max_length=500)
    session_id: str | None = None
    debate_id: str | None = None
    doc_ids: list[str] | None = Field(None, max_length=100)
    custom_instructions: str | None = Field(None, max_length=20000)
    audience: str = Field("Congressional staff", max_length=500)


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
    # May contain a "citation_confidence" key (see services/citation_verifier.py)
    # alongside any other keys already stored in this JSON blob.
    metadata_json: str | None = None


class ReportUpdateRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    status: str | None = None


class ReportDraftRequest(BaseModel):
    title: str = Field(max_length=500)
    report_type: str = Field(max_length=100)
