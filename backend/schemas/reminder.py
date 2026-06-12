from datetime import datetime
from pydantic import BaseModel


class ReminderResponse(BaseModel):
    id: str
    content: str
    due_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}
