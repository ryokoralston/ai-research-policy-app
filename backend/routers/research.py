import asyncio
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import ResearchSession, SearchResult, Document, DocumentChunk
from schemas import ResearchStartRequest, ResearchSessionResponse, ResearchSessionDetail

router = APIRouter(prefix="/api/research", tags=["research"])

# In-memory SSE queues keyed by session_id
_sse_queues: dict[str, asyncio.Queue] = {}


@router.post("/start", response_model=dict)
async def start_research(
    request: ResearchStartRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    session = ResearchSession(
        id=str(uuid.uuid4()),
        query=request.query,
        status="pending",
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    queue: asyncio.Queue = asyncio.Queue()
    _sse_queues[session.id] = queue

    background_tasks.add_task(
        _run_research, session.id, request.query, request.max_sources, queue
    )
    return {"session_id": session.id}


@router.get("/{session_id}/stream")
async def stream_research(session_id: str, db: Session = Depends(get_db)):
    session = db.query(ResearchSession).filter(ResearchSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_generator():
        queue = _sse_queues.get(session_id)
        if not queue:
            yield "event: error\ndata: {\"message\": \"No active stream for this session\"}\n\n"
            return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=60.0)
                yield event
                if '"event_type": "complete"' in event or '"event_type": "error"' in event:
                    break
            except asyncio.TimeoutError:
                yield "event: heartbeat\ndata: {}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/", response_model=list[ResearchSessionResponse])
def list_sessions(limit: int = 20, db: Session = Depends(get_db)):
    sessions = (
        db.query(ResearchSession)
        .order_by(ResearchSession.created_at.desc())
        .limit(limit)
        .all()
    )
    return sessions


@router.get("/{session_id}", response_model=ResearchSessionDetail)
def get_session(session_id: str, db: Session = Depends(get_db)):
    session = db.query(ResearchSession).filter(ResearchSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/{session_id}/save-to-library", response_model=dict)
async def save_session_to_library(
    session_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    session = db.query(ResearchSession).filter(ResearchSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    results = db.query(SearchResult).filter(SearchResult.session_id == session_id).all()
    if not results:
        raise HTTPException(status_code=400, detail="No sources found for this session")

    collection_meta = json.dumps({
        "collection_id": session_id,
        "collection_name": session.query,
    })

    saved = []
    for result in results:
        doc = Document(
            id=str(uuid.uuid4()),
            filename=(result.title or "Untitled") + ".web",
            title=result.title or "Untitled",
            source_type="web",
            url=result.url,
            status="processing",
            metadata_json=collection_meta,
        )
        db.add(doc)
        db.flush()
        saved.append((doc.id, result.full_content or result.ai_summary or result.snippet or ""))

    db.commit()

    for doc_id, content in saved:
        background_tasks.add_task(_index_web_source, doc_id, content)

    return {"saved": len(saved), "collection_id": session_id}


@router.delete("/{session_id}", response_model=dict)
def delete_session(session_id: str, db: Session = Depends(get_db)):
    session = db.query(ResearchSession).filter(ResearchSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(session)
    db.commit()
    return {"deleted": session_id}


async def _index_web_source(doc_id: str, content: str):
    """Background task: chunk and index web content into ChromaDB."""
    from services.embedding_service import EmbeddingService
    from rag.vector_store import VectorStore
    from database import SessionLocal

    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if not doc:
            return

        # Split content into ~800-char chunks on paragraph boundaries
        chunks_text: list[str] = []
        if content.strip():
            paragraphs = content.split("\n\n")
            current = ""
            for para in paragraphs:
                if len(current) + len(para) > 800 and current:
                    chunks_text.append(current.strip())
                    current = para
                else:
                    current = (current + "\n\n" + para).strip() if current else para
            if current:
                chunks_text.append(current.strip())

        if not chunks_text:
            doc.status = "indexed"
            db.commit()
            return

        embed_service = EmbeddingService()
        vs = VectorStore()
        embeddings = embed_service.embed_texts(chunks_text)

        chunk_ids = []
        db_chunks = []
        for i, (text, embedding) in enumerate(zip(chunks_text, embeddings)):
            chunk_id = str(uuid.uuid4())
            chunk_ids.append(chunk_id)
            db_chunks.append(DocumentChunk(
                id=chunk_id,
                document_id=doc_id,
                chunk_index=i,
                content=text,
                token_count=len(text.split()),
                chroma_id=chunk_id,
            ))

        vs.add_chunks(
            chunk_ids=chunk_ids,
            embeddings=embeddings,
            documents=chunks_text,
            metadatas=[
                {"doc_id": doc_id, "page_number": 0, "section_header": "", "chunk_index": i}
                for i in range(len(chunks_text))
            ],
        )

        db.bulk_save_objects(db_chunks)
        doc.status = "indexed"
        doc.word_count = sum(len(t.split()) for t in chunks_text)
        doc.indexed_at = datetime.utcnow()
        db.commit()
    except Exception:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if doc:
            doc.status = "error"
            db.commit()
    finally:
        db.close()


async def _run_research(session_id: str, query: str, max_sources: int, queue: asyncio.Queue):
    """Background task: run the full research pipeline and push SSE events."""
    # Import here to avoid circular deps at module load
    from services.research_agent import run_research_agent
    from database import SessionLocal

    db = SessionLocal()
    try:
        session = db.query(ResearchSession).filter(ResearchSession.id == session_id).first()
        if not session:
            return
        session.status = "running"
        db.commit()

        await run_research_agent(
            session_id=session_id,
            query=query,
            max_sources=max_sources,
            queue=queue,
            db=db,
        )
    except Exception as e:
        import json
        await queue.put(f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n")
        session = db.query(ResearchSession).filter(ResearchSession.id == session_id).first()
        if session:
            session.status = "error"
            db.commit()
    finally:
        db.close()
        _sse_queues.pop(session_id, None)
