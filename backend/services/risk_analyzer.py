"""AI Risk Analysis engine with structured scoring."""
import json
import re
import uuid
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy.orm import Session

from models import RiskAnalysis, ResearchSession
from schemas import AnalysisStartRequest
from services.anthropic_client import generate_text, stream_text, sse_event
from services.research_agent import run_research_agent
from templates import TEMPLATES

import asyncio


def _strip_metadata(content: str, section_title: str = "") -> str:
    """Remove duplicate section headers from section content.
    SCORES_JSON lines are no longer generated — scores are extracted via a
    separate generate_text() call using prefill + stop sequences."""
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

    for section_def in template["sections"]:
        section_key = section_def["key"]
        section_title = section_def["title"]

        yield sse_event("section_start", {"section": section_key, "title": section_title})

        prompt = (
            f"You are conducting a risk assessment of: {request.subject}\n"
            f"Analysis type: {request.analysis_type}\n\n"
        )
        if source_material:
            prompt += f"Research material:\n---\n{source_material[:6000]}\n---\n\n"

        prompt += (
            f"Write the '{section_title}' section of the risk assessment.\n"
            f"Instructions: {section_def['instructions']}\n\n"
            f"Write ONLY the section content in Markdown (no header)."
        )

        section_content = ""
        # temperature=0.3: リスク分析は論理的・再現性重視のため低め
        async for token in stream_text(prompt, system=template["system"], temperature=0.3):
            section_content += token
            yield sse_event("token", {"text": token, "section": section_key})

        # Extract scores using prefill + stop sequences (structured data technique).
        # A separate generate_text() call gets clean JSON with no surrounding text,
        # replacing the old fragile SCORES_JSON regex approach.
        if section_key == "risk_dimensions":
            scores_prompt = (
                f"Based on this risk dimension analysis of '{request.subject}', "
                f"extract the numerical score (1-10) for each dimension.\n\n"
                f"Analysis:\n{section_content}\n\n"
                f"Return a JSON object with exactly these keys: "
                f"capability, deployment, governance, geopolitical, misuse, systemic"
            )
            try:
                scores_raw = await generate_text(
                    scores_prompt,
                    temperature=0.0,        # fully deterministic — scores must be consistent
                    prefill="```json",      # force Claude to open a code fence
                    stop_sequences=["```"],  # stop when code fence closes
                )
                # Strip the markdown fence prefix, then strip surrounding whitespace
                json_str = scores_raw[len("```json"):].strip()
                extracted_scores = json.loads(json_str.strip())
                yield sse_event("scores", {"scores": extracted_scores})
            except (json.JSONDecodeError, Exception):
                pass

        # Strip metadata lines and duplicate section headers from content
        section_content = _strip_metadata(section_content, section_title)

        full_content_parts.append(f"## {section_title}\n\n{section_content}")

        yield sse_event("section_end", {"section": section_key})

    # Save completed analysis
    full_content = f"# Risk Assessment: {request.subject}\n\n" + "\n\n---\n\n".join(full_content_parts)

    analysis = db.query(RiskAnalysis).filter(RiskAnalysis.id == analysis_id).first()
    if analysis:
        analysis.content = full_content
        analysis.risk_scores_json = json.dumps(extracted_scores) if extracted_scores else None
        db.commit()

    yield sse_event("complete", {
        "analysis_id": analysis_id,
        "scores": extracted_scores,
        "word_count": len(full_content.split()),
        "event_type": "complete",
    })
