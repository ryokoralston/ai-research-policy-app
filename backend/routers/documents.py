import os
import re
import uuid
import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from models import Document, DocumentChunk
from schemas import DocumentResponse, DocumentDetail, DocumentAskRequest
from schemas.document import IngestUrlRequest, DocumentFolderRequest, FolderRenameRequest

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".html", ".htm"}


# ── helpers ───────────────────────────────────────────────────────────────────

def _extract_youtube_id(url: str) -> str | None:
    patterns = [
        r"youtube\.com/watch\?.*v=([^&\s]+)",
        r"youtu\.be/([^?\s]+)",
        r"youtube\.com/embed/([^?\s]+)",
        r"youtube\.com/shorts/([^?\s]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


async def _get_youtube_transcript(video_id: str) -> tuple[str, str]:
    """Return (title, transcript_text). Runs sync lib in thread pool."""
    import httpx

    # Fetch title via oEmbed
    title = f"YouTube – {video_id}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://www.youtube.com/oembed",
                params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            )
            if r.status_code == 200:
                title = r.json().get("title", title)
    except Exception:
        pass

    # Fetch transcript in thread (sync library — v1.x API)
    def _fetch():
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt = YouTubeTranscriptApi()
        fetched = ytt.fetch(video_id)
        # FetchedTranscriptSnippet objects have .text attribute
        return " ".join(
            e.text if hasattr(e, "text") else e.get("text", "")
            for e in fetched
        )

    text = await asyncio.to_thread(_fetch)
    return title, text


async def _scrape_url(url: str) -> tuple[str, str]:
    """Scrape a web page and return (title, plain_text)."""
    import httpx
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (compatible; ResearchBot/1.0)"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        r = await client.get(url, headers=headers)

    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    title = url
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    text = soup.get_text(separator="\n", strip=True)
    return title, text


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
    content = await file.read()
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
    result = []
    for doc in docs:
        chunk_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).count()
        resp = DocumentResponse.model_validate(doc)
        resp.chunk_count = chunk_count
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
            doc.metadata_json = json.dumps({
                "collection_id": body.folder_id,
                "collection_name": body.folder_name,
            })
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
                pass
    db.commit()
    return {"updated": updated}


@router.delete("/{doc_id}", response_model=dict)
def delete_document(doc_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove from ChromaDB
    try:
        from rag.vector_store import VectorStore
        vs = VectorStore()
        vs.delete_document(doc_id)
    except Exception:
        pass

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
            request.question, request.doc_ids, request.top_k, db, history
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
