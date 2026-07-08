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


class ChatMessage(BaseModel):
    role: str   # "user" or "assistant"
    # Plain text for simple turns, or block-level content (text/tool_use/tool_result
    # dicts) replayed from a previous turn's "complete" event — see
    # services/anthropic_client.py's serialize_content_blocks.
    content: str | list[dict]


class DocumentAskRequest(BaseModel):
    question: str
    doc_ids: list[str] | None = None  # None = search all documents
    top_k: int = 5
    chat_history: list[ChatMessage] | None = None  # previous turns for multi-turn chat
    custom_system: str | None = None  # optional user-defined system prompt override
    # Cumulative citations from previous turns, used to keep [N] numbering stable
    # across turns (new citations continue numbering after the max existing index).
    prior_citations: list[dict] | None = None


class IngestUrlRequest(BaseModel):
    url: str
