import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models import Document, DocumentChunk
from models.user import User
from schemas import DocumentResponse, DocumentDetail, DocumentAskRequest, DocumentCitedAskRequest
from schemas.document import IngestUrlRequest, DocumentFolderRequest, FolderRenameRequest
from services import audit_log
from services.anthropic_client import IMAGE_MEDIA_TYPES
from services.auth import client_ip, get_current_user
from services.ingestion import _extract_youtube_id, _get_youtube_transcript, _scrape_url
from services.quota import quota_guard

router = APIRouter(prefix="/api/documents", tags=["documents"])

logger = logging.getLogger(__name__)

# Text/document formats plus the image extensions vision-ingestion supports
# (single source of truth: services.anthropic_client.IMAGE_MEDIA_TYPES, also
# used by rag_service.index_document to route images through Claude vision).
ALLOWED_EXTENSIONS = {".pdf", ".txt", ".html", ".htm"} | set(IMAGE_MEDIA_TYPES.keys())

MAX_UPLOAD_BYTES = 25 * 1024 * 1024   # 25 MB cap on uploaded files


def _owned_document(doc_id: str, current_user: User, db: Session) -> Document:
    """Return the caller's document, or 404.

    Another user's document is a 404, not a 403 — a 403 would confirm the id
    exists. Admins included: admin privilege is user/audit administration, not
    access to other people's library.
    """
    doc = (
        db.query(Document)
        .filter(Document.id == doc_id, Document.user_id == current_user.id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


def _owned_doc_ids(current_user: User, db: Session, requested: list[str] | None) -> list[str]:
    """The doc_ids a retrieval request may search: the caller's own documents,
    intersected with `requested` when the caller asked for a subset.

    Always a concrete list, never None — an empty list means "this user has
    nothing to search", which rag/retriever.py honors by returning no chunks
    (None, meaning "search everything", must never reach the retriever from a
    request path).
    """
    owned = {
        row[0] for row in db.query(Document.id).filter(Document.user_id == current_user.id).all()
    }
    if requested:
        return [doc_id for doc_id in requested if doc_id in owned]
    return sorted(owned)


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=dict)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    os.makedirs(settings.uploads_dir, exist_ok=True)

    doc_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Only PDF, TXT, HTML, and image files (PNG, JPG, WEBP, GIF) are supported",
        )

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
        user_id=current_user.id,
        org_id=current_user.org_id,
    )
    db.add(document)
    db.commit()

    background_tasks.add_task(_index_document, doc_id)
    return {"document_id": doc_id, "status": "processing"}


@router.post("/ingest-url", response_model=dict)
async def ingest_url(
    request: IngestUrlRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
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
        logger.exception("URL/YouTube ingestion failed for %s", url)
        raise HTTPException(status_code=422, detail="Could not fetch content from that URL.") from e

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
        user_id=current_user.id,
        org_id=current_user.org_id,
    )
    db.add(document)
    db.commit()

    background_tasks.add_task(_index_document, doc_id)
    return {"document_id": doc_id, "status": "processing", "title": title}


@router.get("/", response_model=list[DocumentResponse])
def list_documents(
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Document).filter(Document.user_id == current_user.id)
    if status:
        q = q.filter(Document.status == status)
    docs = q.order_by(Document.created_at.desc()).all()

    # One aggregate query for all chunk counts instead of one COUNT per document,
    # restricted to the documents actually being returned.
    chunk_counts = dict(
        db.query(DocumentChunk.document_id, func.count(DocumentChunk.id))
        .filter(DocumentChunk.document_id.in_([d.id for d in docs]))
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
def get_document(
    doc_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = _owned_document(doc_id, current_user, db)
    chunk_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).count()
    resp = DocumentDetail.model_validate(doc)
    resp.chunk_count = chunk_count
    return resp


@router.post("/assign-folder", response_model=dict)
def assign_folder(
    body: DocumentFolderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import json
    updated = 0
    for doc_id in body.doc_ids:
        # Ids come from the request body, so this write path is scoped like
        # every read path: ids the caller does not own are silently skipped.
        doc = (
            db.query(Document)
            .filter(Document.id == doc_id, Document.user_id == current_user.id)
            .first()
        )
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
def rename_folder(
    body: FolderRenameRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import json
    updated = 0
    for doc in db.query(Document).filter(Document.user_id == current_user.id).all():
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
def delete_document(
    doc_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = _owned_document(doc_id, current_user, db)
    filename = doc.filename

    # Index cleanup comes FIRST, and a failure aborts the whole delete.
    #
    # The invariant (see rag/reconcile.py): no Chroma or BM25 entry may exist
    # without a backing `document_chunks` row. This ordering is what enforces
    # it. The previous "best effort" version deleted the DB rows even when
    # cleanup raised, which stranded index entries whose chunks no longer
    # existed — still retrievable and citable, with nothing left to verify
    # them against.
    #
    # Keeping the rows on failure is the safe direction: entries that still
    # have their chunk rows are, by definition, not orphans. The document
    # simply stays in the library and the caller retries. A retry is harmless
    # even when Chroma succeeded and BM25 failed — deleting already-gone
    # entries from either store is a no-op.
    try:
        from rag.vector_store import VectorStore
        VectorStore().delete_document(doc_id)
    except Exception:
        logger.warning(
            "ChromaDB cleanup failed for document %s — aborting delete, DB rows kept",
            doc_id, exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Search index cleanup failed; the document was not deleted. Please retry.",
        )

    try:
        from rag.lexical_index import LexicalIndex
        LexicalIndex().delete_document(doc_id)
    except Exception:
        logger.warning(
            "BM25 index cleanup failed for document %s — aborting delete, DB rows kept",
            doc_id, exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="Search index cleanup failed; the document was not deleted. Please retry.",
        )

    # Both indexes are clean — only now is the delete allowed to proceed, so
    # the audit entry never records a deletion that did not happen.
    if doc.file_path and os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    audit_log.record(db, user=current_user, action="document.delete", resource_type="document",
                      resource_id=doc_id, detail=filename, ip_address=client_ip(request))

    db.delete(doc)
    db.commit()
    return {"deleted": doc_id}


@router.post("/ask", dependencies=[Depends(quota_guard("documents.ask"))])
async def ask_documents(
    request: DocumentAskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    history = (
        [{"role": m.role, "content": m.content} for m in request.chat_history]
        if request.chat_history else None
    )
    # Retrieval is confined to the caller's own documents (see _owned_doc_ids);
    # the chat loop's other stateful tools — reminders and the draft workspace —
    # are keyed to the caller by the user passed through here.
    doc_ids = _owned_doc_ids(current_user, db, request.doc_ids)

    async def event_generator():
        from services.rag_service import answer_question
        async for event in answer_question(
            request.question, doc_ids, request.top_k, db, history, request.custom_system,
            prior_citations=request.prior_citations, user=current_user,
        ):
            yield event

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/{doc_id}/ask-citations", dependencies=[Depends(quota_guard("documents.ask_citations"))])
async def ask_document_citations(
    doc_id: str,
    request: DocumentCitedAskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Single-document Q&A with API-native citations (see
    services/document_qa.py) — distinct from /ask above, which searches
    across the whole (or a selected subset of the) document library via a
    tool-use loop and assigns its own sentence-level [N] citations."""
    # 404 before any work happens if the document isn't the caller's.
    _owned_document(doc_id, current_user, db)

    async def event_generator():
        from services.document_qa import ask_document_with_citations
        async for event in ask_document_with_citations(doc_id, request.question, db):
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
