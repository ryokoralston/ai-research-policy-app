import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from models import Report, ReportSection
from schemas import (
    ReportGenerateRequest, ReportResponse, ReportDetail, ReportUpdateRequest, ReportDraftRequest
)

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("/generate")
async def generate_report(request: ReportGenerateRequest, db: Session = Depends(get_db)):
    report_id = str(uuid.uuid4())
    report = Report(
        id=report_id,
        title=request.title,
        report_type=request.report_type,
        status="draft",
        session_id=request.session_id,
    )
    db.add(report)
    db.commit()

    async def event_generator():
        from services.report_generator import generate_report_stream
        async for event in generate_report_stream(report_id, request, db):
            yield event

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/template/{report_type}")
def get_template(report_type: str):
    from templates import TEMPLATES
    template = TEMPLATES.get(report_type)
    if not template:
        raise HTTPException(status_code=404, detail=f"Unknown report type: {report_type}")
    return {
        "report_type": report_type,
        "sections": [
            {"key": s["key"], "title": s["title"], "instructions": s["instructions"]}
            for s in template["sections"]
        ],
    }


@router.post("/draft", response_model=dict)
def create_draft(request: ReportDraftRequest, db: Session = Depends(get_db)):
    report_id = str(uuid.uuid4())
    report = Report(
        id=report_id,
        title=request.title,
        report_type=request.report_type,
        status="draft",
    )
    db.add(report)
    db.commit()
    return {"report_id": report_id}


@router.get("/", response_model=list[ReportResponse])
def list_reports(db: Session = Depends(get_db)):
    reports = db.query(Report).order_by(Report.created_at.desc()).all()
    return reports


@router.get("/{report_id}", response_model=ReportDetail)
def get_report(report_id: str, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.patch("/{report_id}", response_model=ReportResponse)
def update_report(report_id: str, request: ReportUpdateRequest, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if request.title is not None:
        report.title = request.title
    if request.content is not None:
        report.content = request.content
        report.word_count = len(request.content.split())
    if request.status is not None:
        allowed = {"draft", "in_review", "pre_approval", "completed"}
        if request.status in allowed:
            report.status = request.status
    report.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(report)
    return report


@router.get("/{report_id}/export")
def export_report(
    report_id: str,
    format: str = Query(default="txt", regex="^(txt|pdf)$"),
    db: Session = Depends(get_db),
):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    content = report.content or ""
    ascii_title = re.sub(r'[^\x00-\x7F]', '', report.title).strip()
    safe_title = re.sub(r'[^\w\s-]', '', ascii_title).strip().replace(' ', '_') or report_id[:8]

    if format == "txt":
        plain = _markdown_to_plain(content)
        return Response(
            content=plain,
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.txt"'},
        )

    pdf_bytes = _generate_pdf(report.title, content)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.pdf"'},
    )


def _markdown_to_plain(text: str) -> str:
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`{3}.*?`{3}', '', text, flags=re.DOTALL)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    text = re.sub(r'^[-*]\s+', '- ', text, flags=re.MULTILINE)
    return text.strip()


def _generate_pdf(title: str, content: str) -> bytes:
    from utils.pdf_renderer import make_pdf, render_markdown

    pdf, font, left, usable_w = make_pdf()

    pdf.set_font(font, "B", 18)
    pdf.set_x(left)
    pdf.multi_cell(usable_w, 10, title)
    pdf.ln(2)
    pdf.set_draw_color(100, 120, 200)
    pdf.line(left, pdf.get_y(), left + usable_w, pdf.get_y())
    pdf.set_draw_color(0, 0, 0)
    pdf.ln(5)

    render_markdown(pdf, font, left, usable_w, content)

    return bytes(pdf.output())


@router.delete("/{report_id}", response_model=dict)
def delete_report(report_id: str, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    db.delete(report)
    db.commit()
    return {"deleted": report_id}
