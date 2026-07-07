import logging
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models import Document, DocumentChunk
from schemas import DocumentResponse, DocumentDetail, DocumentAskRequest
from schemas.document import IngestUrlRequest, DocumentFolderRequest, FolderRenameRequest
from services.ingestion import _extract_youtube_id, _get_youtube_transcript, _scrape_url

router = APIRouter(prefix="/api/documents", tags=["documents"])

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".html", ".htm"}

MAX_UPLOAD_BYTES = 25 * 1024 * 1024   # 25 MB cap on uploaded files


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=dict)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    os.makedirs(settings.uploads_dir, exist_ok=True)

    doc_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only PDF, TXT, and HTML files are supported")

    file_path = os.path.join(settings.uploads_dir, f"{doc_id}{ext}")
    # Read with a hard cap to avoid loading an unbounded file into memory.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )
    with open(file_path, "wb") as f:
        f.write(content)

    document = Document(
        id=doc_id,
        filename=file.filename or f"unknown{ext}",
        title=None,
        source_type="upload",
        file_path=file_path,
        status="processing",
    )
    db.add(document)
    db.commit()

    background_tasks.add_task(_index_document, doc_id)
    return {"document_id": doc_id, "status": "processing"}


@router.post("/ingest-url", response_model=dict)
async def ingest_url(
    request: IngestUrlRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    url = request.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")

    settings = get_settings()
    os.makedirs(settings.uploads_dir, exist_ok=True)

    yt_id = _extract_youtube_id(url)
    try:
        if yt_id:
            source_type = "youtube"
            title, text = await _get_youtube_transcript(yt_id)
        else:
            source_type = "url"
            title, text = await _scrape_url(url)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to fetch content: {e}")

    if not text.strip():
        raise HTTPException(status_code=422, detail="No text content could be extracted from this URL")

    doc_id = str(uuid.uuid4())
    file_path = os.path.join(settings.uploads_dir, f"{doc_id}.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(text)

    document = Document(
        id=doc_id,
        filename=f"{title[:120]}.txt",
        title=title,
        source_type=source_type,
        file_path=file_path,
        url=url,
        status="processing",
    )
    db.add(document)
    db.commit()

    background_tasks.add_task(_index_document, doc_id)
    return {"document_id": doc_id, "status": "processing", "title": title}


@router.get("/", response_model=list[DocumentResponse])
def list_documents(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Document)
    if status:
        q = q.filter(Document.status == status)
    docs = q.order_by(Document.created_at.desc()).all()

    # One aggregate query for all chunk counts instead of one COUNT per document.
    chunk_counts = dict(
        db.query(DocumentChunk.document_id, func.count(DocumentChunk.id))
        .group_by(DocumentChunk.document_id)
        .all()
    )

    result = []
    for doc in docs:
        resp = DocumentResponse.model_validate(doc)
        resp.chunk_count = chunk_counts.get(doc.id, 0)
        result.append(resp)
    return result


@router.get("/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    chunk_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count()
    resp = DocumentDetail.model_validate(doc)
    resp.chunk_count = chunk_count
    return resp


@router.post("/assign-folder", response_model=dict)
def assign_folder(body: DocumentFolderRequest, db: Session = Depends(get_db)):
    import json
    updated = 0
    for doc_id in body.doc_ids:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if doc:
            # Merge into existing metadata instead of overwriting it wholesale,
            # so any other keys a document may carry survive a folder assignment.
            meta = {}
            if doc.metadata_json:
                try:
                    parsed = json.loads(doc.metadata_json)
                    if isinstance(parsed, dict):
                        meta = parsed
                except Exception:
                    logger.warning(
                        "Overwriting malformed metadata_json for document %s during folder assignment",
                        doc.id, exc_info=True,
                    )
            meta["collection_id"] = body.folder_id
            meta["collection_name"] = body.folder_name
            doc.metadata_json = json.dumps(meta)
            updated += 1
    db.commit()
    return {"updated": updated}


@router.post("/rename-folder", response_model=dict)
def rename_folder(body: FolderRenameRequest, db: Session = Depends(get_db)):
    import json
    updated = 0
    for doc in db.query(Document).all():
        if doc.metadata_json:
            try:
                meta = json.loads(doc.metadata_json)
                if meta.get("collection_id") == body.folder_id:
                    meta["collection_name"] = body.new_name
                    doc.metadata_json = json.dumps(meta)
                    updated += 1
            except Exception:
                logger.warning(
                    "Skipping document %s with malformed metadata_json during folder rename",
                    doc.id, exc_info=True,
                )
    db.commit()
    return {"updated": updated}


@router.delete("/{doc_id}", response_model=dict)
def delete_document(doc_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove from ChromaDB (best effort — the DB delete proceeds regardless)
    try:
        from rag.vector_store import VectorStore
        vs = VectorStore()
        vs.delete_document(doc_id)
    except Exception:
        logger.warning(
            "ChromaDB cleanup failed for document %s — continuing with DB delete",
            doc_id, exc_info=True,
        )

    # Remove file
    if doc.file_path and os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    db.delete(doc)
    db.commit()
    return {"deleted": doc_id}


@router.post("/ask")
async def ask_documents(request: DocumentAskRequest, db: Session = Depends(get_db)):
    history = (
        [{"role": m.role, "content": m.content} for m in request.chat_history]
        if request.chat_history else None
    )

    async def event_generator():
        from services.rag_service import answer_question
        async for event in answer_question(
            request.question, request.doc_ids, request.top_k, db, history, request.custom_system
        ):
            yield event

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def _index_document(doc_id: str):
    """Background task: chunk and index a document into ChromaDB."""
    from services.rag_service import index_document
    from database import SessionLocal
    db = SessionLocal()
    try:
        await index_document(doc_id, db)
    finally:
        db.close()
