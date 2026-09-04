import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db, get_or_init_model_settings
from models import ResearchSession, SearchResult, Document
from models.user import User
from schemas import ResearchStartRequest, ResearchSessionResponse, ResearchSessionDetail
from services.auth import get_current_user
from services.model_catalog import allowed_model_ids
from services.quota import quota_guard
from services.usage import usage_context
from utils.sse import queue_event_stream, sse_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", tags=["research"])

# In-memory SSE queues keyed by session_id
_sse_queues: dict[str, asyncio.Queue] = {}


def _owned_session(session_id: str, current_user: User, db: Session) -> ResearchSession:
    """Return the caller's research session, or 404.

    Someone else's session is a 404, never a 403 — a 403 would confirm the id
    exists. Admins get no exemption: their privilege covers user and audit
    administration, not other people's research.
    """
    session = (
        db.query(ResearchSession)
        .filter(ResearchSession.id == session_id, ResearchSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post("/start", response_model=dict, dependencies=[Depends(quota_guard("research"))])
async def start_research(
    request: ResearchStartRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if request.model is not None:
        # The research page seeds its model picker from the currently
        # configured main_model (see frontend/src/app/research/page.tsx) and
        # always sends it explicitly, so the presently-stored value (even a
        # pre-allowlist default like ModelSettings.main_model's
        # "claude-opus-4-6", or one an admin picked before the catalog last
        # refreshed) must keep working. Only a value that is neither in the
        # catalog/fallback allowlist nor the currently configured model is
        # rejected — that's the fabricated/stale case (e.g. "gpt-4o") D-1 is
        # closing, not a routine resend of the default.
        ms = get_or_init_model_settings(db)
        if request.model not in allowed_model_ids(db) | {ms.main_model, ms.fast_model}:
            raise HTTPException(status_code=400, detail="Unknown model id.")

    session = ResearchSession(
        id=str(uuid.uuid4()),
        query=request.query,
        status="pending",
        user_id=current_user.id,
        org_id=current_user.org_id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    queue: asyncio.Queue = asyncio.Queue()
    _sse_queues[session.id] = queue

    # The user id travels with the task: the run happens after this request
    # returns, so it can't read the caller off the request context.
    background_tasks.add_task(
        _run_research, session.id, request.query, request.max_sources,
        request.model, current_user.id, queue,
    )
    return {"session_id": session.id}


@router.get("/{session_id}/stream")
async def stream_research(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _owned_session(session_id, current_user, db)

    async def event_generator():
        queue = _sse_queues.get(session_id)
        if not queue:
            yield sse_event("error", {"message": "No active stream for this session"})
            return
        async for event in queue_event_stream(queue, timeout_seconds=60.0):
            yield event

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/", response_model=list[ResearchSessionResponse])
def list_sessions(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sessions = (
        db.query(ResearchSession)
        .filter(ResearchSession.user_id == current_user.id)
        .order_by(ResearchSession.created_at.desc())
        .limit(limit)
        .all()
    )
    responses = []
    for session in sessions:
        latest_report = max(session.reports, key=lambda r: r.created_at, default=None)
        resp = ResearchSessionResponse.model_validate(session)
        resp.latest_report_id = latest_report.id if latest_report else None
        responses.append(resp)
    return responses


@router.get("/{session_id}", response_model=ResearchSessionDetail)
def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _owned_session(session_id, current_user, db)


@router.post("/{session_id}/save-to-library", response_model=dict)
async def save_session_to_library(
    session_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _owned_session(session_id, current_user, db)

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
            user_id=current_user.id,
            org_id=current_user.org_id,
        )
        db.add(doc)
        db.flush()
        saved.append((doc.id, result.full_content or result.ai_summary or result.snippet or ""))

    db.commit()

    for doc_id, content in saved:
        background_tasks.add_task(_index_web_source, doc_id, content)

    return {"saved": len(saved), "collection_id": session_id}


@router.delete("/{session_id}", response_model=dict)
def delete_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _owned_session(session_id, current_user, db)
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


async def _run_research(
    session_id: str, query: str, max_sources: int, model: str | None,
    user_id: str, queue: asyncio.Queue,
):
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

        # Every provider call the agent makes — decomposition, per-source
        # summaries, synthesis, the gap-closing rounds, and the Tavily searches
        # — is attributed to this user and feature.
        with usage_context(user_id=user_id, feature="research"):
            await run_research_agent(
                session_id=session_id,
                query=query,
                max_sources=max_sources,
                model=model,
                queue=queue,
                db=db,
            )
    except Exception:
        logger.exception("Research failed for session %s", session_id)
        await queue.put(sse_event("error", {"message": "Research failed. Please try again."}))
        session = db.query(ResearchSession).filter(ResearchSession.id == session_id).first()
        if session:
            session.status = "error"
            db.commit()
    finally:
        db.close()
        _sse_queues.pop(session_id, None)
