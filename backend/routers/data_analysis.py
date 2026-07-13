import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from services.data_analysis import (
    ALLOWED_ANALYSIS_EXTENSIONS,
    MAX_ANALYSIS_FILE_BYTES,
    analyze_data_stream,
    upload_analysis_file,
)

router = APIRouter(prefix="/api/datalab", tags=["datalab"])

logger = logging.getLogger(__name__)


@router.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    question: str = Form(...),
):
    """Upload a data file and stream a Claude code-execution analysis of it
    (see services/data_analysis.py for the SSE event contract)."""
    import os

    filename = file.filename or "data"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_ANALYSIS_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Only {', '.join(sorted(ALLOWED_ANALYSIS_EXTENSIONS))} files are supported",
        )

    # Read with a hard cap to avoid loading an unbounded file into memory.
    content = await file.read(MAX_ANALYSIS_FILE_BYTES + 1)
    if len(content) > MAX_ANALYSIS_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {MAX_ANALYSIS_FILE_BYTES // (1024 * 1024)} MB)",
        )

    try:
        file_id = await upload_analysis_file(content, filename)
    except Exception as exc:
        logger.exception("Data Lab file upload failed")
        raise HTTPException(status_code=502, detail=f"Upload to Claude failed: {exc}")

    async def event_generator():
        async for event in analyze_data_stream(file_id, question, filename):
            yield event

    return StreamingResponse(event_generator(), media_type="text/event-stream")
