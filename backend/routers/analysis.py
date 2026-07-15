import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from models import RiskAnalysis
from schemas import AnalysisStartRequest, RiskAnalysisResponse
from utils.export import markdown_to_plain, render_pdf

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.post("/start")
async def start_analysis(request: AnalysisStartRequest, db: Session = Depends(get_db)):
    analysis_id = str(uuid.uuid4())
    analysis = RiskAnalysis(
        id=analysis_id,
        subject=request.subject,
        analysis_type=request.analysis_type,
    )
    db.add(analysis)
    db.commit()

    async def event_generator():
        from services.risk_analyzer import run_risk_analysis
        async for event in run_risk_analysis(analysis_id, request, db):
            yield event

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/", response_model=list[RiskAnalysisResponse])
def list_analyses(db: Session = Depends(get_db)):
    analyses = db.query(RiskAnalysis).order_by(RiskAnalysis.created_at.desc()).all()
    return analyses


@router.get("/{analysis_id}", response_model=RiskAnalysisResponse)
def get_analysis(analysis_id: str, db: Session = Depends(get_db)):
    analysis = db.query(RiskAnalysis).filter(RiskAnalysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return analysis


@router.get("/{analysis_id}/export")
def export_analysis(
    analysis_id: str,
    format: str = Query(default="txt", pattern="^(txt|pdf)$"),
    db: Session = Depends(get_db),
):
    analysis = db.query(RiskAnalysis).filter(RiskAnalysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    content = analysis.content or ""
    safe_title = re.sub(r"[^\w\s-]", "", analysis.subject).strip().replace(" ", "_") or analysis_id[:8]

    if format == "txt":
        # Note: bullets are now normalized to "- " like the report export
        # (previously the analysis txt export left bullet markers untouched).
        plain = markdown_to_plain(content)
        return Response(
            content=plain,
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.txt"'},
        )

    return Response(
        content=render_pdf(analysis.subject, content),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.pdf"'},
    )


@router.delete("/{analysis_id}")
def delete_analysis(analysis_id: str, db: Session = Depends(get_db)):
    analysis = db.query(RiskAnalysis).filter(RiskAnalysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    db.delete(analysis)
    db.commit()
    return {"deleted": analysis_id}
