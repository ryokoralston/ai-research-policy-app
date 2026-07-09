from datetime import datetime
from pydantic import BaseModel


class WorkspaceFileInfo(BaseModel):
    name: str
    size_bytes: int
    modified_at: datetime


class WorkspaceFileContent(BaseModel):
    name: str
    content: str
