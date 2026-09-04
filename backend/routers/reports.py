import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from models import Report, ResearchSession
from models.user import User
from schemas import (
    ReportGenerateRequest, ReportResponse, ReportDetail, ReportUpdateRequest, ReportDraftRequest
)
from services.auth import get_current_user
from services.quota import quota_guard
from services.usage import usage_context
from utils.export import markdown_to_plain, render_pdf

router = APIRouter(prefix="/api/reports", tags=["reports"])

ALLOWED_REPORT_STATUSES = {"draft", "in_review", "pre_approval", "completed"}


def _owned_report(report_id: str, current_user: User, db: Session) -> Report:
    """Return the caller's report, or 404.

    Every per-report endpoint goes through this. A report that exists but
    belongs to someone else is a 404, not a 403 — a 403 would confirm the id
    is real. Admins are not exempt: role grants user/audit administration,
    never other people's content.
    """
    report = (
        db.query(Report)
        .filter(Report.id == report_id, Report.user_id == current_user.id)
        .first()
    )
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.post("/generate", dependencies=[Depends(quota_guard("report"))])
async def generate_report(
    request: ReportGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # session_id is persisted on the row, so it is validated here rather than
    # only where source material is read: attaching a report to someone else's
    # research session would put a foreign row into that session's report list.
    if request.session_id:
        owns_session = (
            db.query(ResearchSession)
            .filter(
                ResearchSession.id == request.session_id,
                ResearchSession.user_id == current_user.id,
            )
            .first()
        )
        if not owns_session:
            raise HTTPException(status_code=404, detail="Session not found")

    report_id = str(uuid.uuid4())
    report = Report(
        id=report_id,
        title=request.title,
        report_type=request.report_type,
        status="draft",
        session_id=request.session_id,
        user_id=current_user.id,
        org_id=current_user.org_id,
    )
    db.add(report)
    db.commit()

    async def event_generator():
        from services.report_generator import generate_report_stream
        with usage_context(user_id=current_user.id, feature="report"):
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
def create_draft(
    request: ReportDraftRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    report_id = str(uuid.uuid4())
    report = Report(
        id=report_id,
        title=request.title,
        report_type=request.report_type,
        status="draft",
        user_id=current_user.id,
        org_id=current_user.org_id,
    )
    db.add(report)
    db.commit()
    return {"report_id": report_id}


@router.get("/", response_model=list[ReportResponse])
def list_reports(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    reports = (
        db.query(Report)
        .filter(Report.user_id == current_user.id)
        .order_by(Report.created_at.desc())
        .all()
    )
    return reports


@router.get("/{report_id}", response_model=ReportDetail)
def get_report(
    report_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _owned_report(report_id, current_user, db)


@router.patch("/{report_id}", response_model=ReportResponse)
def update_report(
    report_id: str,
    request: ReportUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    report = _owned_report(report_id, current_user, db)
    # Validate before applying anything so an invalid status doesn't partially
    # apply the other fields in the same request.
    if request.status is not None and request.status not in ALLOWED_REPORT_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid status '{request.status}'. "
                f"Allowed values: {', '.join(sorted(ALLOWED_REPORT_STATUSES))}"
            ),
        )
    if request.title is not None:
        report.title = request.title
    if request.content is not None:
        report.content = request.content
        report.word_count = len(request.content.split())
    if request.status is not None:
        report.status = request.status
    report.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(report)
    return report


@router.get("/{report_id}/export")
def export_report(
    report_id: str,
    format: str = Query(default="txt", pattern="^(txt|pdf)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    report = _owned_report(report_id, current_user, db)

    content = report.content or ""
    ascii_title = re.sub(r'[^\x00-\x7F]', '', report.title).strip()
    safe_title = re.sub(r'[^\w\s-]', '', ascii_title).strip().replace(' ', '_') or report_id[:8]

    if format == "txt":
        plain = markdown_to_plain(content)
        return Response(
            content=plain,
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.txt"'},
        )

    pdf_bytes = render_pdf(report.title, content)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.pdf"'},
    )


@router.delete("/{report_id}", response_model=dict)
def delete_report(
    report_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    report = _owned_report(report_id, current_user, db)
    db.delete(report)
    db.commit()
    return {"deleted": report_id}
