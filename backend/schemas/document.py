from datetime import datetime
from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: str
    filename: str
    title: str | None
    source_type: str
    url: str | None
    page_count: int | None
    word_count: int | None
    status: str
    created_at: datetime
    indexed_at: datetime | None
    chunk_count: int = 0
    metadata_json: str | None

    model_config = {"from_attributes": True}


class DocumentDetail(DocumentResponse):
    file_path: str | None


class DocumentFolderRequest(BaseModel):
    doc_ids: list[str]
    folder_id: str
    folder_name: str


class FolderRenameRequest(BaseModel):
    folder_id: str
    new_name: str


class DocumentAskRequest(BaseModel):
    question: str
    doc_ids: list[str] | None = None  # None = search all documents
    top_k: int = 5


class IngestUrlRequest(BaseModel):
    url: str
