import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from models import RiskAnalysis
from schemas import AnalysisStartRequest, RiskAnalysisResponse

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
    format: str = Query(default="txt", regex="^(txt|pdf)$"),
    db: Session = Depends(get_db),
):
    analysis = db.query(RiskAnalysis).filter(RiskAnalysis.id == analysis_id).first()
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    content = analysis.content or ""
    safe_title = re.sub(r"[^\w\s-]", "", analysis.subject).strip().replace(" ", "_") or analysis_id[:8]

    if format == "txt":
        plain = _markdown_to_plain(content)
        return Response(
            content=plain,
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.txt"'},
        )

    from utils.pdf_renderer import make_pdf, render_markdown
    pdf, font, left, usable_w = make_pdf()
    pdf.set_font(font, "B", 18)
    pdf.set_x(left)
    pdf.multi_cell(usable_w, 10, analysis.subject)
    pdf.ln(2)
    pdf.set_draw_color(100, 120, 200)
    pdf.line(left, pdf.get_y(), left + usable_w, pdf.get_y())
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(5)
    render_markdown(pdf, font, left, usable_w, content)
    return Response(
        content=bytes(pdf.output()),
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


def _markdown_to_plain(text: str) -> str:
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`{3}.*?`{3}", "", text, flags=re.DOTALL)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    return text.strip()
