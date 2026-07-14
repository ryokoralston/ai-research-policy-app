"""Section-by-section report generation with SSE streaming."""
import json
import logging
import re
import uuid
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy.orm import Session

from models import Report, ReportSection, ResearchSession, Document, DocumentChunk, Debate, DebateArgument
from schemas import ReportGenerateRequest
from services.anthropic_client import stream_text_with_thinking, sse_event, UNTRUSTED_CONTENT_GUARD
from services.citation_verifier import verify_grounding
from services.report_quality import revise_if_ungrounded
from templates import TEMPLATES

logger = logging.getLogger(__name__)


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
    # Source material is untrusted (web/docs/debate) — guard against injection.
    system_prompt = f"{system_prompt}\n\n{UNTRUSTED_CONTENT_GUARD}"

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

    # Shared/cacheable prefix: report framing (title/type/audience) + the
    # (large) source material — byte-identical across every section call
    # below. Passed as cached_context so the section loop's repeated calls
    # reuse a single cache write instead of paying full price for
    # source_material on every section.
    shared_context = _build_shared_context(
        report_title=request.title,
        report_type=request.report_type,
        audience=request.audience,
        source_material=source_material,
    )

    # ── Generate sections: body first, "summarize_body" sections last ────────
    # A summary section (e.g. congressional_brief's executive_summary,
    # policy_memo's bluf) is supposed to distill the report it leads — but a
    # naive top-to-bottom loop generates it FIRST, from raw source material,
    # before any body section exists to summarize. previous_sections also only
    # ever carries the last 2 sections truncated to 500 chars, so even a later
    # section never sees a full body. Fix: partition into body sections and
    # deferred ("summarize_body": True) sections, preserving template order
    # within each group. Generate every body section exactly as before
    # (previous_sections continuity untouched, and deferred sections never
    # enter that continuity). Only once the full body exists, generate each
    # deferred section from the complete assembled body via
    # _build_summary_section_prompt. SSE events therefore stream in
    # GENERATION order (deferred last), but canonical_parts/full_content and
    # each ReportSection.order_index use the section's index in
    # template["sections"] — i.e. display/document order, summary first.
    canonical_sections = list(enumerate(template["sections"]))
    body_sections = [(i, s) for i, s in canonical_sections if not s.get("summarize_body")]
    deferred_sections = [(i, s) for i, s in canonical_sections if s.get("summarize_body")]

    all_sections: list[dict] = []  # body sections only, in generation order — previous_sections continuity
    canonical_parts: list[str | None] = [None] * len(template["sections"])

    for canonical_index, section_def in body_sections:
        section_key = section_def["key"]
        section_title = section_def["title"]

        yield sse_event("section_start", {"section": section_key, "title": section_title})

        prompt = _build_section_prompt(
            section_def=section_def,
            custom_instructions=request.custom_instructions,
            previous_sections=all_sections,
            word_budget=word_budgets[canonical_index] if word_budgets else None,
        )

        section_content = ""
        async for kind, token in stream_text_with_thinking(
            prompt, system=system_prompt, cached_context=shared_context, usage_log_tag="report-section",
        ):
            if kind == "thinking":
                yield sse_event("thinking", {"text": token, "section": section_key})
                continue
            section_content += token
            yield sse_event("token", {"text": token, "section": section_key})

        # Strip internal metadata lines (e.g. SCORES_JSON) before saving
        section_content = _strip_scores_json_lines(section_content)

        # Save section to DB — order_index is the section's canonical
        # template position, not its generation order.
        section_record = ReportSection(
            id=str(uuid.uuid4()),
            report_id=report_id,
            section_key=section_key,
            title=section_title,
            content=section_content,
            order_index=canonical_index,
        )
        db.add(section_record)
        db.commit()

        all_sections.append({"key": section_key, "title": section_title, "content": section_content})
        canonical_parts[canonical_index] = f"## {section_title}\n\n{section_content}"

        yield sse_event("section_end", {"section": section_key, "word_count": len(section_content.split())})

    # Full assembled body (canonical order) — this is what deferred
    # "summarize_body" sections below are asked to faithfully distill.
    report_body_markdown = "\n\n---\n\n".join(
        canonical_parts[i] for i, _ in body_sections
    )

    for canonical_index, section_def in deferred_sections:
        section_key = section_def["key"]
        section_title = section_def["title"]

        yield sse_event("section_start", {"section": section_key, "title": section_title})

        prompt = _build_summary_section_prompt(
            section_def=section_def,
            report_body=report_body_markdown,
            custom_instructions=request.custom_instructions,
            word_budget=word_budgets[canonical_index] if word_budgets else None,
        )

        section_content = ""
        async for kind, token in stream_text_with_thinking(
            prompt, system=system_prompt, cached_context=shared_context, usage_log_tag="report-section",
        ):
            if kind == "thinking":
                yield sse_event("thinking", {"text": token, "section": section_key})
                continue
            section_content += token
            yield sse_event("token", {"text": token, "section": section_key})

        section_content = _strip_scores_json_lines(section_content)

        # Note: deferred sections are intentionally NOT appended to
        # `all_sections` — body generation is already complete, and later
        # deferred sections (if any) must not see this one's content either,
        # keeping "previous_sections never contains a summary" invariant.
        section_record = ReportSection(
            id=str(uuid.uuid4()),
            report_id=report_id,
            section_key=section_key,
            title=section_title,
            content=section_content,
            order_index=canonical_index,
        )
        db.add(section_record)
        db.commit()

        canonical_parts[canonical_index] = f"## {section_title}\n\n{section_content}"

        yield sse_event("section_end", {"section": section_key, "word_count": len(section_content.split())})

    total_sections_generated = len(body_sections) + len(deferred_sections)

    # ── Finalize report ───────────────────────────────────────────────────────
    full_content = f"# {request.title}\n\n" + "\n\n---\n\n".join(canonical_parts)

    # Citation/grounding verification: one extra LLM-as-judge call checking whether
    # full_content is actually supported by source_material. Skipped if there's no
    # source material; a failure degrades gracefully (logged, continue without it)
    # rather than blocking the save/complete flow.
    citation_confidence: dict | None = None
    if source_material:
        try:
            citation_confidence = await verify_grounding(full_content, source_material)
        except Exception:
            logger.warning(
                "Citation verification failed for report %r — continuing without it",
                report_id,
                exc_info=True,
            )

    # Evaluator-optimizer feedback loop (services/report_quality.py): if the grader
    # above flagged unsupported claims, attempt one bounded revision pass and keep
    # whichever version (original or revised) scores at least as well. When
    # citation_confidence is None (no source material, or verification failed),
    # revise_if_ungrounded short-circuits to a single "final" event with the
    # original content — no extra API calls.
    final_content = full_content
    final_grade = citation_confidence
    async for kind, payload in revise_if_ungrounded(
        full_content, source_material, citation_confidence,
        system_prompt=system_prompt, cached_context=shared_context, usage_log_tag="report-revision",
    ):
        if kind == "revision_start":
            yield sse_event("revision_start", payload)
        elif kind == "token":
            yield sse_event("token", {"text": payload, "section": "revision"})
        elif kind == "thinking":
            yield sse_event("thinking", {"text": payload, "section": "revision"})
        elif kind == "revision_end":
            yield sse_event("revision_end", payload)
        elif kind == "final":
            final_content = payload["content"]
            final_grade = payload["grade"]

    word_count = len(final_content.split())

    # report.content is the canonical, possibly-revised report saved below. The
    # ReportSection rows saved during the loop above keep the pre-revision text —
    # they're the generation-time record, not re-synced after a revision.
    report = db.query(Report).filter(Report.id == report_id).first()
    if report:
        report.content = final_content
        report.status = "completed"
        report.word_count = word_count
        report.updated_at = datetime.utcnow()
        if final_grade:
            report.metadata_json = _merge_metadata_json(
                report.metadata_json, {"citation_confidence": final_grade}
            )
        db.commit()

    if final_grade:
        yield sse_event("verification", {
            "confidence_score": final_grade.get("confidence_score"),
            "unsupported_claims": final_grade.get("unsupported_claims", []),
        })

    yield sse_event("complete", {
        "report_id": report_id,
        "word_count": word_count,
        "sections": total_sections_generated,
        "citation_confidence": final_grade,
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

    # Single call — caching gives nothing across calls here, but split out and
    # pass the source material as cached_context anyway for structural
    # consistency with the section loop above; harmless for a single request.
    shared_context = _build_single_pass_shared_context(source_material)

    prompt = (
        f"Write a complete '{request.report_type.replace('_', ' ').title()}' report.\n"
        f"Title: {request.title}\n"
        f"Audience: {request.audience}\n\n"
        f"Structure the report with these sections: {', '.join(section_titles)}\n\n"
        f"Format in clean Markdown with ## section headers.\n\n"
        f"⚠️ STRICT REQUIREMENT: The TOTAL word count of the entire report MUST be "
        f"{target_words} words or fewer. This is a hard limit — stop writing before you reach it."
    )
    if request.custom_instructions:
        prompt += f"\n\nAdditional constraints: {request.custom_instructions}"

    yield sse_event("section_start", {"section": "full_report", "title": "Report"})

    full_content_raw = ""
    async for kind, token in stream_text_with_thinking(
        prompt, system=system_prompt, cached_context=shared_context, usage_log_tag="report-section",
    ):
        if kind == "thinking":
            yield sse_event("thinking", {"text": token, "section": "full_report"})
            continue
        full_content_raw += token
        yield sse_event("token", {"text": token, "section": "full_report"})

    full_content = f"# {request.title}\n\n{full_content_raw}"

    # Citation/grounding verification — same integration point as the section-by-
    # section path above (this function duplicates that path's save/complete
    # logic already; not deduplicating further here per scope).
    citation_confidence: dict | None = None
    if source_material:
        try:
            citation_confidence = await verify_grounding(full_content, source_material)
        except Exception:
            logger.warning(
                "Citation verification failed for report %r — continuing without it",
                report_id,
                exc_info=True,
            )

    # Evaluator-optimizer feedback loop — same integration point as the section-by-
    # section path above (see services/report_quality.py and the comment there).
    final_content = full_content
    final_grade = citation_confidence
    async for kind, payload in revise_if_ungrounded(
        full_content, source_material, citation_confidence,
        system_prompt=system_prompt, cached_context=shared_context, usage_log_tag="report-revision",
    ):
        if kind == "revision_start":
            yield sse_event("revision_start", payload)
        elif kind == "token":
            yield sse_event("token", {"text": payload, "section": "revision"})
        elif kind == "thinking":
            yield sse_event("thinking", {"text": payload, "section": "revision"})
        elif kind == "revision_end":
            yield sse_event("revision_end", payload)
        elif kind == "final":
            final_content = payload["content"]
            final_grade = payload["grade"]

    word_count = len(final_content.split())

    # report.content is the canonical, possibly-revised report. There are no
    # ReportSection rows in this single-pass path (word-limit generation writes
    # the whole report in one call), so there's nothing else to keep in sync.
    report = db.query(Report).filter(Report.id == report_id).first()
    if report:
        report.content = final_content
        report.status = "completed"
        report.word_count = word_count
        report.updated_at = datetime.utcnow()
        if final_grade:
            report.metadata_json = _merge_metadata_json(
                report.metadata_json, {"citation_confidence": final_grade}
            )
        db.commit()

    if final_grade:
        yield sse_event("verification", {
            "confidence_score": final_grade.get("confidence_score"),
            "unsupported_claims": final_grade.get("unsupported_claims", []),
        })

    yield sse_event("complete", {
        "report_id": report_id,
        "word_count": word_count,
        "sections": 1,
        "citation_confidence": final_grade,
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


def _merge_metadata_json(existing_json: str | None, updates: dict) -> str:
    """Merge `updates` into the existing Report.metadata_json blob without clobbering
    other keys that may already be stored there.

    Mirrors the merge-not-overwrite pattern already established in this codebase for
    Document.metadata_json in routers/documents.py's assign_folder (F-5): parse the
    existing blob, merge in the new keys, re-serialize. If the existing value is
    malformed JSON, fall back to overwriting the whole blob with just `updates`,
    logged as a warning — same fallback precedent as F-5.
    """
    meta: dict = {}
    if existing_json:
        try:
            parsed = json.loads(existing_json)
            if isinstance(parsed, dict):
                meta = parsed
        except Exception:
            logger.warning(
                "Overwriting malformed Report.metadata_json while saving citation_confidence",
                exc_info=True,
            )
    meta.update(updates)
    return json.dumps(meta)


def _strip_scores_json_lines(content: str) -> str:
    """Remove internal metadata lines (e.g. SCORES_JSON) from section content."""
    lines = content.splitlines()
    filtered = [line for line in lines if not line.strip().startswith("SCORES_JSON:")]
    return "\n".join(filtered).strip()


def _extract_word_limit(custom_instructions: str | None) -> int | None:
    """Extract a total word count limit from custom instructions if present."""
    if not custom_instructions:
        return None
    patterns = [
        r'limit\w*\s+(?:\w+\s+){0,3}?to\s*(\d+)\s*words?',
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


def _build_shared_context(
    report_title: str,
    report_type: str,
    audience: str,
    source_material: str,
) -> str:
    """Byte-identical-across-sections prefix of the section prompt: report
    framing (title/type/audience) plus the (large) source material. Passed as
    `cached_context` to stream_text_with_thinking (see generate_report_stream)
    so every section call within one report reuses a single cache write
    across this prefix instead of paying full price for source_material on
    every section call.

    Must render identically for every section of a given report — nothing
    that varies by section belongs here (see _build_section_prompt for the
    per-section remainder). Concatenating this function's output with
    _build_section_prompt's output reproduces exactly the single prompt
    string this module built before the cached_context split.
    """
    return (
        f"You are writing a '{report_type.replace('_', ' ').title()}' report.\n"
        f"Report title: {report_title}\n"
        f"Target audience: {audience}\n\n"
        f"<source_material>\n{source_material[:8000]}\n</source_material>\n"
    )


def _build_single_pass_shared_context(source_material: str) -> str:
    """Cacheable prefix for the single-pass path (_generate_single_pass): just
    the source material. Caching gives nothing across calls here (a single
    request per report), but splitting it out keeps this path structurally
    consistent with the section loop's cached_context usage — harmless.
    """
    return f"<source_material>\n{source_material[:8000]}\n</source_material>\n\n"


def _word_budget_note(word_budget: int | None) -> str:
    """Shared word-budget suffix used by both _build_section_prompt and
    _build_summary_section_prompt. Factored out so the two prompt builders
    stay in sync without duplicating the wording; _build_section_prompt's
    output bytes are unchanged by this extraction (see
    test_report_word_limits.test_concatenated_shared_and_section_prompt_matches_pre_split_text)."""
    return (
        f"\n\n⚠️ WORD LIMIT: Write this section in {word_budget} words or fewer. "
        f"This overrides any word count in the instructions above."
        if word_budget else ""
    )


def _custom_constraints_note(custom_instructions: str | None) -> str:
    """Shared MANDATORY USER CONSTRAINTS suffix used by both _build_section_prompt
    and _build_summary_section_prompt. See _word_budget_note for why this is
    factored out."""
    return (
        f"\n\n⚠️ MANDATORY USER CONSTRAINTS (override all other instructions): {custom_instructions}"
        if custom_instructions else ""
    )


def _build_section_prompt(
    section_def: dict,
    custom_instructions: str | None,
    previous_sections: list[dict],
    word_budget: int | None = None,
) -> str:
    """Per-section remainder of the prompt: previous_sections grows and
    section_def/word_budget vary every call, so this is sent as the varying
    `prompt` argument alongside the shared, cached context built by
    _build_shared_context (see generate_report_stream).

    previous_sections must never contain a "summarize_body" section (e.g.
    executive_summary, BLUF) — those are generated after every body section,
    in a separate deferred pass (see generate_report_stream), so a body
    section can never end up "building on" a summary of itself."""
    prev_context = ""
    if previous_sections:
        prev_context = "\n\nPrevious sections already written:\n" + "\n\n".join(
            f"### {s['title']}\n{s['content'][:500]}..." for s in previous_sections[-2:]
        )

    return (
        f"{prev_context}\n\n"
        f"Now write the '{section_def['title']}' section.\n"
        f"Instructions: {section_def['instructions']}\n\n"
        f"Write ONLY the section content in Markdown (no section header — it will be added automatically). "
        f"Do not repeat information already covered in previous sections."
        f"{_word_budget_note(word_budget)}"
        f"{_custom_constraints_note(custom_instructions)}"
    )


def _build_summary_section_prompt(
    section_def: dict,
    report_body: str,
    custom_instructions: str | None,
    word_budget: int | None = None,
) -> str:
    """Prompt for a "summarize_body" section (e.g. executive_summary, BLUF).

    Unlike _build_section_prompt, this is used only for sections generated in
    the deferred pass of generate_report_stream, AFTER every body section
    already exists — so the summary can be a faithful distillation of the
    actual report rather than an independent pass over raw source material
    written before the report it claims to summarize exists. `report_body` is
    the full assembled body markdown (canonical template order), not a
    truncated preview — unlike _build_section_prompt's previous_sections,
    which only keeps the last 2 sections at 500 chars each."""
    return (
        f"The complete report body has already been written and is provided below.\n\n"
        f"<report_body>\n{report_body}\n</report_body>\n\n"
        f"Now write the '{section_def['title']}' section as a faithful distillation of the report body above. "
        f"Do not introduce any claim, statistic, or recommendation that does not appear in the report body above. "
        f"Instructions: {section_def['instructions']}\n\n"
        f"Write ONLY the section content in Markdown (no section header — it will be added automatically)."
        f"{_word_budget_note(word_budget)}"
        f"{_custom_constraints_note(custom_instructions)}"
    )
