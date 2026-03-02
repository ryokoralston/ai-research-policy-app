from datetime import datetime
from pydantic import BaseModel


class DebateStartRequest(BaseModel):
    topic: str
    persona_keys: list[str] | None = None  # None = all 10


class DebateArgumentResponse(BaseModel):
    id: str
    persona_key: str
    persona_name: str
    round_number: int
    round_name: str
    content: str
    order_index: int
    created_at: datetime

    model_config = {"from_attributes": True}


class DebateResponse(BaseModel):
    id: str
    topic: str
    status: str
    personas: str | None
    synthesis: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class DebateDetail(DebateResponse):
    arguments: list[DebateArgumentResponse] = []
