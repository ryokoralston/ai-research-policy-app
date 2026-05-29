"""Section-by-section report generation with SSE streaming."""
import json
import re
import uuid
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy.orm import Session

from models import Report, ReportSection, ResearchSession, Document, DocumentChunk, Debate, DebateArgument
from schemas import ReportGenerateRequest
from services.anthropic_client import stream_text, sse_event
from templates import TEMPLATES


async def generate_report_stream(
    report_id: str,
    request: ReportGenerateRequest,
    db: Session,
) -> AsyncIterator[str]:
    template = TEMPLATES.get(request.report_type)
    if not template:
        yield sse_event("error", {"message": f"Unknown report type: {request.report_type}"})
        return

    # ── Gather source material ────────────────────────────────────────────────
    source_material = await _gather_source_material(request, db)

    if not source_material:
        yield sse_event("error", {"message": "No source material found. Provide a session_id, debate_id, or doc_ids."})
        return

    # ── Build effective system prompt ─────────────────────────────────────────
    system_prompt = template["system"]
    if request.custom_instructions:
        system_prompt = (
            f"MANDATORY USER CONSTRAINTS — You MUST follow these above all else:\n"
            f"{request.custom_instructions}\n\n---\n\n"
            f"{system_prompt}"
        )

    # ── word limit detected → single-pass generation ──────────────────────────
    word_limit = _extract_word_limit(request.custom_instructions)
    if word_limit:
        async for event in _generate_single_pass(
            report_id, request, db, source_material, system_prompt, template, word_limit,
        ):
            yield event
        return

    # ── Calculate per-section word budgets if a total word limit is specified ──
    word_budgets = _calculate_word_budgets(template["sections"], request.custom_instructions)

    # ── Generate sections one by one ─────────────────────────────────────────
    all_sections: list[dict] = []
    full_content_parts: list[str] = []

    for i, section_def in enumerate(template["sections"]):
        section_key = section_def["key"]
        section_title = section_def["title"]

        yield sse_event("section_start", {"section": section_key, "title": section_title})

        prompt = _build_section_prompt(
            section_def=section_def,
            report_title=request.title,
            report_type=request.report_type,
            audience=request.audience,
            source_material=source_material,
            custom_instructions=request.custom_instructions,
            previous_sections=all_sections,
            word_budget=word_budgets[i] if word_budgets else None,
        )

        section_content = ""
        async for token in stream_text(prompt, system=system_prompt):
            section_content += token
            yield sse_event("token", {"text": token, "section": section_key})

        # Strip internal metadata lines (e.g. SCORES_JSON) before saving
        section_content = _strip_metadata(section_content)

        # Save section to DB
        section_record = ReportSection(
            id=str(uuid.uuid4()),
            report_id=report_id,
            section_key=section_key,
            title=section_title,
            content=section_content,
            order_index=len(all_sections),
        )
        db.add(section_record)
        db.commit()

        all_sections.append({"key": section_key, "title": section_title, "content": section_content})
        full_content_parts.append(f"## {section_title}\n\n{section_content}")

        yield sse_event("section_end", {"section": section_key, "word_count": len(section_content.split())})

    # ── Finalize report ───────────────────────────────────────────────────────
    full_content = f"# {request.title}\n\n" + "\n\n---\n\n".join(full_content_parts)
    word_count = len(full_content.split())

    report = db.query(Report).filter(Report.id == report_id).first()
    if report:
        report.content = full_content
        report.status = "complete"
        report.word_count = word_count
        report.updated_at = datetime.utcnow()
        db.commit()

    yield sse_event("complete", {
        "report_id": report_id,
        "word_count": word_count,
        "sections": len(all_sections),
        "event_type": "complete",
    })


async def _generate_single_pass(
    report_id: str,
    request: ReportGenerateRequest,
    db: Session,
    source_material: str,
    system_prompt: str,
    template: dict,
    word_limit: int,
) -> AsyncIterator[str]:
    """Generate the entire report in one API call to reliably respect word limits."""
    section_titles = [s["title"] for s in template["sections"]]

    # Apply 85% buffer to account for Claude's tendency to overshoot word counts
    target_words = max(30, round(word_limit * 0.85))

    prompt = (
        f"Write a complete '{request.report_type.replace('_', ' ').title()}' report.\n"
        f"Title: {request.title}\n"
        f"Audience: {request.audience}\n\n"
        # "Structure with XML tags" lesson: descriptive tag instead of --- fences
        f"<source_material>\n{source_material[:8000]}\n</source_material>\n\n"
        f"Structure the report with these sections: {', '.join(section_titles)}\n\n"
        f"Format in clean Markdown with ## section headers.\n\n"
        f"⚠️ STRICT REQUIREMENT: The TOTAL word count of the entire report MUST be "
        f"{target_words} words or fewer. This is a hard limit — stop writing before you reach it."
    )
    if request.custom_instructions:
        prompt += f"\n\nAdditional constraints: {request.custom_instructions}"

    yield sse_event("section_start", {"section": "full_report", "title": "Report"})

    full_content_raw = ""
    async for token in stream_text(prompt, system=system_prompt):
        full_content_raw += token
        yield sse_event("token", {"text": token, "section": "full_report"})

    full_content = f"# {request.title}\n\n{full_content_raw}"
    word_count = len(full_content.split())

    report = db.query(Report).filter(Report.id == report_id).first()
    if report:
        report.content = full_content
        report.status = "complete"
        report.word_count = word_count
        report.updated_at = datetime.utcnow()
        db.commit()

    yield sse_event("complete", {
        "report_id": report_id,
        "word_count": word_count,
        "sections": 1,
        "event_type": "complete",
    })


async def _gather_source_material(request: ReportGenerateRequest, db: Session) -> str:
    """Build a source material string from session, debate, or documents."""
    parts: list[str] = []

    if request.session_id:
        session = db.query(ResearchSession).filter(
            ResearchSession.id == request.session_id
        ).first()
        if session:
            if session.summary:
                parts.append(f"## Research Synthesis\n{session.summary}")
            for i, result in enumerate(session.results[:10], 1):
                if result.ai_summary:
                    parts.append(
                        f"## Source {i}: {result.title}\nURL: {result.url}\n{result.ai_summary}"
                    )

    if request.debate_id:
        debate = db.query(Debate).filter(Debate.id == request.debate_id).first()
        if debate:
            parts.append(f"## Policy Debate Transcript\nTopic: {debate.topic}\n")
            # Group arguments by round
            args = (
                db.query(DebateArgument)
                .filter(DebateArgument.debate_id == debate.id)
                .order_by(DebateArgument.order_index)
                .all()
            )
            last_round = None
            for arg in args:
                if arg.round_number != last_round:
                    parts.append(f"### Round {arg.round_number}: {arg.round_name}")
                    last_round = arg.round_number
                parts.append(f"**{arg.persona_name}**: {arg.content}")
            if debate.synthesis:
                parts.append(f"### Moderator Synthesis\n{debate.synthesis}")

    if request.doc_ids:
        for doc_id in request.doc_ids:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                continue
            # Get first few chunks as representative content
            chunks = (
                db.query(DocumentChunk)
                .filter(DocumentChunk.document_id == doc_id)
                .order_by(DocumentChunk.chunk_index)
                .limit(10)
                .all()
            )
            if chunks:
                doc_content = "\n\n".join(c.content for c in chunks)
                parts.append(f"## Document: {doc.title or doc.filename}\n{doc_content[:3000]}")

    return "\n\n---\n\n".join(parts)


def _strip_metadata(content: str) -> str:
    """Remove internal metadata lines (e.g. SCORES_JSON) from section content."""
    lines = content.splitlines()
    filtered = [line for line in lines if not line.strip().startswith("SCORES_JSON:")]
    return "\n".join(filtered).strip()


def _extract_word_limit(custom_instructions: str | None) -> int | None:
    """Extract a total word count limit from custom instructions if present."""
    if not custom_instructions:
        return None
    patterns = [
        r'(\d+)\s*words?\s*(?:or\s*(?:less|fewer)|以下|以内|max|maximum)',
        r'(?:under|within|max|maximum|at\s*most)\s*(\d+)\s*words?',
        r'(\d+)\s*words?\s*(?:total|以下|以内)',
        r'(\d+)\s*(?:words?|ワード|語)\s*以下',
        r'(\d+)\s*(?:words?|ワード|語)\s*以内',
    ]
    for pattern in patterns:
        match = re.search(pattern, custom_instructions, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _calculate_word_budgets(sections: list[dict], custom_instructions: str | None) -> list[int] | None:
    """Proportionally distribute a total word limit across sections."""
    total_limit = _extract_word_limit(custom_instructions)
    if not total_limit:
        return None

    default_counts = []
    for sec in sections:
        match = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*words?', sec["instructions"], re.IGNORECASE)
        if match:
            mid = (int(match.group(1)) + int(match.group(2))) // 2
        else:
            match = re.search(r'(\d+)\s*words?', sec["instructions"], re.IGNORECASE)
            mid = int(match.group(1)) if match else 80
        default_counts.append(mid)

    total_default = sum(default_counts)
    budgets = [max(15, round(count * total_limit / total_default)) for count in default_counts]
    return budgets


def _build_section_prompt(
    section_def: dict,
    report_title: str,
    report_type: str,
    audience: str,
    source_material: str,
    custom_instructions: str | None,
    previous_sections: list[dict],
    word_budget: int | None = None,
) -> str:
    prev_context = ""
    if previous_sections:
        prev_context = "\n\nPrevious sections already written:\n" + "\n\n".join(
            f"### {s['title']}\n{s['content'][:500]}..." for s in previous_sections[-2:]
        )

    # If a word budget is calculated, override the section word count directly
    word_budget_note = (
        f"\n\n⚠️ WORD LIMIT: Write this section in {word_budget} words or fewer. "
        f"This overrides any word count in the instructions above."
        if word_budget else ""
    )

    custom_override = (
        f"\n\n⚠️ MANDATORY USER CONSTRAINTS (override all other instructions): {custom_instructions}"
        if custom_instructions else ""
    )

    return (
        f"You are writing a '{report_type.replace('_', ' ').title()}' report.\n"
        f"Report title: {report_title}\n"
        f"Target audience: {audience}\n\n"
        f"<source_material>\n{source_material[:8000]}\n</source_material>\n"
        f"{prev_context}\n\n"
        f"Now write the '{section_def['title']}' section.\n"
        f"Instructions: {section_def['instructions']}\n\n"
        f"Write ONLY the section content in Markdown (no section header — it will be added automatically). "
        f"Do not repeat information already covered in previous sections."
        f"{word_budget_note}"
        f"{custom_override}"
    )
