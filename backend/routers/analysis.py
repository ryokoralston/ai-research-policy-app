import json
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from models import RiskAnalysis
from models.user import User
from schemas import AnalysisStartRequest, RiskAnalysisResponse, SourceRef
from services.auth import get_current_user
from services.quota import quota_guard
from services.analysis_sources import (
    dimension_citations,
    format_score_summary_markdown,
    format_sources_markdown,
    resolve_sources,
)
from utils.export import markdown_to_plain, render_pdf

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _owned_analysis(analysis_id: str, current_user: User, db: Session) -> RiskAnalysis:
    """Return the caller's risk analysis, or 404 — another user's analysis is
    reported as missing rather than forbidden, and admins get no exemption."""
    analysis = (
        db.query(RiskAnalysis)
        .filter(RiskAnalysis.id == analysis_id, RiskAnalysis.user_id == current_user.id)
        .first()
    )
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return analysis


@router.post("/start", dependencies=[Depends(quota_guard("analysis"))])
async def start_analysis(
    request: AnalysisStartRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    analysis_id = str(uuid.uuid4())
    analysis = RiskAnalysis(
        id=analysis_id,
        subject=request.subject,
        analysis_type=request.analysis_type,
        user_id=current_user.id,
        org_id=current_user.org_id,
    )
    db.add(analysis)
    db.commit()

    async def event_generator():
        from services.risk_analyzer import run_risk_analysis
        async for event in run_risk_analysis(analysis_id, request, db):
            yield event

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/", response_model=list[RiskAnalysisResponse])
def list_analyses(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    analyses = (
        db.query(RiskAnalysis)
        .filter(RiskAnalysis.user_id == current_user.id)
        .order_by(RiskAnalysis.created_at.desc())
        .all()
    )
    return analyses


@router.get("/{analysis_id}", response_model=RiskAnalysisResponse)
def get_analysis(
    analysis_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    analysis = _owned_analysis(analysis_id, current_user, db)
    response = RiskAnalysisResponse.model_validate(analysis)
    # Resolved here rather than on the model so the list endpoint doesn't pay
    # a source lookup per analysis. Built into SourceRef explicitly: pydantic
    # does not validate on assignment, so assigning raw dicts would let a
    # shape change slip through to serialization time.
    sources = resolve_sources(db, analysis)
    response.sources = [SourceRef(**s) for s in sources]
    response.dimension_citations = dimension_citations(analysis.content or "", sources)
    return response


@router.get("/{analysis_id}/export")
def export_analysis(
    analysis_id: str,
    format: str = Query(default="txt", pattern="^(txt|pdf)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    analysis = _owned_analysis(analysis_id, current_user, db)

    # Both formats gain the two things the UI shows but the document never
    # did: the score summary that qualifies each inline "Score: X/10", and the
    # citation list without which every [Source N] marker is unidentifiable.
    sources = resolve_sources(db, analysis)
    content = (analysis.content or "") + format_score_summary_markdown(
        json.loads(analysis.risk_scores_json) if analysis.risk_scores_json else {},
        json.loads(analysis.dimension_confidence_json) if analysis.dimension_confidence_json else {},
        dimension_citations(analysis.content or "", sources),
    ) + format_sources_markdown(sources)
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
def delete_analysis(
    analysis_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    analysis = _owned_analysis(analysis_id, current_user, db)
    db.delete(analysis)
    db.commit()
    return {"deleted": analysis_id}
