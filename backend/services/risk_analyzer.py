"""AI Risk Analysis engine with structured scoring."""
import json
import logging
import re
import uuid
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy.orm import Session

from models import RiskAnalysis, ResearchSession
from schemas import AnalysisStartRequest
from services.anthropic_client import generate_json, stream_text_with_thinking, sse_event, UNTRUSTED_CONTENT_GUARD
from services.citation_verifier import verify_grounding
from services.research_agent import run_research_agent
from templates import TEMPLATES

import asyncio

logger = logging.getLogger(__name__)


def _build_shared_context(subject: str, analysis_type: str, source_material: str) -> str:
    """Byte-identical-across-sections prefix of the section prompt: the
    subject/analysis-type framing plus the (potentially large) research
    material. Passed as `cached_context` to stream_text_with_thinking (see
    run_risk_analysis) so every section call within one analysis reuses a
    single cache write across this prefix instead of paying full price for
    source_material on every section.

    Must render identically for every section of a given analysis — nothing
    that varies by section belongs here (see _build_section_prompt for the
    per-section remainder). Concatenating this function's output with
    _build_section_prompt's output reproduces exactly the single prompt
    string this module built before the cached_context split.
    """
    context = (
        f"You are conducting a risk assessment of: {subject}\n"
        f"Analysis type: {analysis_type}\n\n"
    )
    if source_material:
        context += f"<research_material>\n{source_material[:6000]}\n</research_material>\n\n"
    return context


def _build_section_prompt(section_title: str, instructions: str) -> str:
    """Per-section remainder of the prompt: section_title/instructions vary
    every call, so this is sent as the varying `prompt` argument alongside
    the shared, cached context built by _build_shared_context."""
    return (
        f"Write the '{section_title}' section of the risk assessment.\n"
        f"Instructions: {instructions}\n\n"
        f"Write ONLY the section content in Markdown (no header)."
    )


def _strip_duplicate_heading(content: str, section_title: str = "") -> str:
    """Remove duplicate section headers from section content.
    (Named distinctly from report_generator._strip_scores_json_lines — the two
    used to share the name _strip_metadata while doing different things.)"""
    lines = content.splitlines()
    filtered = []
    for line in lines:
        stripped = line.strip()
        # Remove duplicate section header (e.g. "## Risk Dimensions" or "# Risk Dimensions")
        if section_title and re.match(r'^#{1,3}\s*' + re.escape(section_title) + r'\s*$', stripped, re.IGNORECASE):
            continue
        filtered.append(line)
    return "\n".join(filtered).strip()


async def run_risk_analysis(
    analysis_id: str,
    request: AnalysisStartRequest,
    db: Session,
) -> AsyncIterator[str]:
    template = TEMPLATES["risk_assessment"]

    source_material = request.context or ""

    # Optionally run web research first
    if request.run_web_research:
        yield sse_event("status", {"message": f"Researching '{request.subject}'..."})

        session = ResearchSession(
            id=str(uuid.uuid4()),
            query=f"AI risk analysis: {request.subject}",
            status="pending",
        )
        db.add(session)
        db.commit()

        queue: asyncio.Queue = asyncio.Queue()
        await run_research_agent(
            session_id=session.id,
            query=f"risks, concerns, governance challenges of {request.subject}",
            max_sources=5,
            queue=queue,
            db=db,
        )

        # Drain queue (already completed)
        db.refresh(session)
        if session.summary:
            source_material = session.summary

        analysis = db.query(RiskAnalysis).filter(RiskAnalysis.id == analysis_id).first()
        if analysis:
            analysis.session_id = session.id
            db.commit()

    yield sse_event("status", {"message": "Generating risk assessment..."})

    # Generate sections
    full_content_parts: list[str] = []
    extracted_scores: dict = {}

    # Shared/cacheable prefix: subject + analysis type + research material —
    # byte-identical across every section call below. Passed as
    # cached_context so the section loop reuses a single cache write instead
    # of paying full price for source_material on every section.
    shared_context = _build_shared_context(request.subject, request.analysis_type, source_material)

    for section_def in template["sections"]:
        section_key = section_def["key"]
        section_title = section_def["title"]

        yield sse_event("section_start", {"section": section_key, "title": section_title})

        prompt = _build_section_prompt(section_title, section_def["instructions"])

        section_content = ""
        # temperature=0.3 (logical/reproducible output for risk analysis) is no
        # longer passed here — adaptive thinking rejects a non-default
        # temperature (400 from the API). Thinking is on by default now, which
        # gives its own consistency benefit for this reasoning-heavy section.
        # System guard: research_material is untrusted — treat it as data only.
        section_system = template["system"] + "\n\n" + UNTRUSTED_CONTENT_GUARD
        async for kind, token in stream_text_with_thinking(
            prompt, system=section_system, cached_context=shared_context, usage_log_tag="risk-section",
        ):
            if kind == "thinking":
                yield sse_event("thinking", {"text": token, "section": section_key})
                continue
            section_content += token
            yield sse_event("token", {"text": token, "section": section_key})

        # Extract scores using prefill + stop sequences (structured data technique).
        # A separate generate_text() call gets clean JSON with no surrounding text,
        # replacing the old fragile SCORES_JSON regex approach.
        if section_key == "risk_dimensions":
            scores_prompt = (
                f"Based on this risk dimension analysis of '{request.subject}', "
                f"extract the numerical score (1-10) for each dimension.\n\n"
                f"<dimension_analysis>\n{section_content}\n</dimension_analysis>\n\n"
                f"Return a JSON object with exactly these keys: "
                f"capability, deployment, governance, geopolitical, misuse, systemic"
            )
            try:
                # temperature=0.0: fully deterministic — scores must be consistent
                extracted_scores = await generate_json(scores_prompt, temperature=0.0)
                yield sse_event("scores", {"scores": extracted_scores})
            except Exception:
                logger.warning(
                    "Risk score extraction failed for %r — continuing without scores",
                    request.subject,
                    exc_info=True,
                )

        # Strip duplicate section headers from content
        section_content = _strip_duplicate_heading(section_content, section_title)

        full_content_parts.append(f"## {section_title}\n\n{section_content}")

        yield sse_event("section_end", {"section": section_key})

    # Save completed analysis
    full_content = f"# Risk Assessment: {request.subject}\n\n" + "\n\n---\n\n".join(full_content_parts)

    # Citation/grounding verification: one extra LLM-as-judge call checking whether
    # full_content is actually supported by source_material. Skipped entirely if
    # there's no source material to verify against; a failure degrades gracefully
    # (logged, continue without it) rather than blocking the save/complete flow —
    # same style as the scores-extraction failure handling above.
    citation_confidence: dict | None = None
    if source_material:
        try:
            citation_confidence = await verify_grounding(full_content, source_material)
        except Exception:
            logger.warning(
                "Citation verification failed for %r — continuing without it",
                request.subject,
                exc_info=True,
            )

    analysis = db.query(RiskAnalysis).filter(RiskAnalysis.id == analysis_id).first()
    if analysis:
        analysis.content = full_content
        analysis.risk_scores_json = json.dumps(extracted_scores) if extracted_scores else None
        analysis.citation_confidence_json = (
            json.dumps(citation_confidence) if citation_confidence else None
        )
        db.commit()

    if citation_confidence:
        yield sse_event("verification", {
            "confidence_score": citation_confidence.get("confidence_score"),
            "unsupported_claims": citation_confidence.get("unsupported_claims", []),
        })

    yield sse_event("complete", {
        "analysis_id": analysis_id,
        "scores": extracted_scores,
        "citation_confidence": citation_confidence,
        "word_count": len(full_content.split()),
        "event_type": "complete",
    })
