import asyncio
import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models.debate import Debate
from schemas.debate import DebateStartRequest, DebateResponse, DebateDetail
from templates.personas import PERSONAS
from utils.sse import queue_event_stream, sse_event

router = APIRouter(prefix="/api/debate", tags=["debate"])

# In-memory SSE queues keyed by debate_id
_sse_queues: dict[str, asyncio.Queue] = {}

DEFAULT_PERSONA_ORDER = [
    "safety_researcher", "tech_ceo", "military", "civil_rights", "intl_relations",
    "economist", "ethicist", "regulator", "global_south", "accelerationist",
]


@router.post("/start", response_model=dict)
async def start_debate(
    request: DebateStartRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    # Validate and resolve persona keys
    if request.persona_keys:
        invalid = [k for k in request.persona_keys if k not in PERSONAS]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Unknown persona keys: {invalid}")
        persona_keys = [k for k in DEFAULT_PERSONA_ORDER if k in request.persona_keys]
    else:
        persona_keys = DEFAULT_PERSONA_ORDER

    if len(persona_keys) < 2:
        raise HTTPException(status_code=400, detail="At least 2 personas required.")

    debate = Debate(
        id=str(uuid.uuid4()),
        topic=request.topic.strip(),
        status="pending",
        personas=json.dumps(persona_keys),
    )
    db.add(debate)
    db.commit()
    db.refresh(debate)

    queue: asyncio.Queue = asyncio.Queue()
    _sse_queues[debate.id] = queue

    background_tasks.add_task(_run_debate_task, debate.id, debate.topic, persona_keys, queue)
    return {"debate_id": debate.id}


@router.get("/{debate_id}/stream")
async def stream_debate(debate_id: str, db: Session = Depends(get_db)):
    debate = db.query(Debate).filter(Debate.id == debate_id).first()
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")

    async def event_generator():
        queue = _sse_queues.get(debate_id)
        if not queue:
            yield sse_event("error", {"message": "No active stream for this debate"})
            return
        async for event in queue_event_stream(queue, timeout_seconds=120.0):
            yield event

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/", response_model=list[DebateResponse])
def list_debates(limit: int = 20, db: Session = Depends(get_db)):
    debates = (
        db.query(Debate)
        .order_by(Debate.created_at.desc())
        .limit(limit)
        .all()
    )
    return debates


@router.get("/{debate_id}", response_model=DebateDetail)
def get_debate(debate_id: str, db: Session = Depends(get_db)):
    debate = db.query(Debate).filter(Debate.id == debate_id).first()
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")
    return debate


@router.delete("/{debate_id}", response_model=dict)
def delete_debate(debate_id: str, db: Session = Depends(get_db)):
    debate = db.query(Debate).filter(Debate.id == debate_id).first()
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")
    db.delete(debate)
    db.commit()
    _sse_queues.pop(debate_id, None)
    return {"deleted": debate_id}


async def _run_debate_task(debate_id: str, topic: str, persona_keys: list[str], queue: asyncio.Queue):
    """Background task: run the full debate and push SSE events."""
    from services.debate_service import run_debate

    try:
        await run_debate(debate_id, topic, persona_keys, queue)
    except Exception as e:
        await queue.put(sse_event("error", {"message": str(e), "event_type": "error"}))
    finally:
        _sse_queues.pop(debate_id, None)
