import asyncio
import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import ResearchSession, SearchResult, Document
from schemas import ResearchStartRequest, ResearchSessionResponse, ResearchSessionDetail
from utils.sse import queue_event_stream, sse_event

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
            yield sse_event("error", {"message": "No active stream for this session"})
            return
        async for event in queue_event_stream(queue, timeout_seconds=60.0):
            yield event

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
    """Background task: index web content via the shared RAG pipeline."""
    from services.rag_service import index_web_content
    from database import SessionLocal

    db = SessionLocal()
    try:
        await index_web_content(doc_id, content, db)
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
        await queue.put(sse_event("error", {"message": str(e)}))
        session = db.query(ResearchSession).filter(ResearchSession.id == session_id).first()
        if session:
            session.status = "error"
            db.commit()
    finally:
        db.close()
        _sse_queues.pop(session_id, None)
