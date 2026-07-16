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
from services.tavily_client import TavilyClient
from templates import TEMPLATES, RISK_DIMENSIONS

import asyncio

logger = logging.getLogger(__name__)

# Per-dimension weak-grounding re-research loop (see _fix_weak_dimensions):
# a fourth instance of this codebase's bounded evaluator-optimizer pattern
# (report_quality.py's revise_if_ungrounded, research_agent.py's
# MAX_GAP_ITERATIONS, debate_service.py's CONSENSUS_DIVERGENCE_THRESHOLD).
#
# WEAK_DIMENSION_THRESHOLD — citation_verifier.py's 0-10 scale is documented
# as 8-10 well-grounded, 5-7 "a few specific unsupported claims but overall
# sound", 0-4 fabricated/contradicting; a score below 6 is treated as worth
# one bounded extra-research pass (catches the 0-4 tier and the bottom of the
# 5-7 tier; 6-7 is left alone as "good enough" to bound how often this fires).
WEAK_DIMENSION_THRESHOLD = 6

# MAX_DIMENSIONS_TO_FIX — bounded like every other loop in this codebase:
# even if all 6 dimensions grade weak, only the 2 lowest-scoring get the
# extra search+regenerate+re-grade treatment (one Tavily search + one LLM
# generation + one grading call each), so worst-case added cost per analysis
# is fixed regardless of how bad the initial pass is.
MAX_DIMENSIONS_TO_FIX = 2


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


def _build_dimension_prompt(dimension: dict) -> str:
    """Prompt for ONE risk dimension's parallel analysis call (see
    _analyze_dimension / run_risk_analysis). The subject/analysis-type/
    research-material framing is NOT repeated here — it lives in
    shared_context, passed separately as `cached_context` to
    stream_text_with_thinking, so this string only ever varies by dimension.

    Mentions exactly one dimension's title — never any of the other five —
    so the 6 parallel calls stay independent and don't bleed into each
    other's output. Requests the exact output format
    run_risk_analysis expects when assembling section_content:
        ### {title}
        Score: X/10 (brief justification)
        {2-3 sentence analysis}
    """
    criteria_lines = "\n".join(f"- {c}" for c in dimension["criteria"])
    return (
        f"Assess ONLY the following single risk dimension for the subject "
        f"described above. Do not discuss, list, or mention any other risk "
        f"dimension.\n\n"
        f"Dimension: **{dimension['title']}** {dimension['scale']}\n\n"
        f"Consider these factors in your analysis:\n{criteria_lines}\n\n"
        f"Respond in exactly this format, with no preamble and no other "
        f"headers:\n"
        f"### {dimension['title']}\n"
        f"Score: X/10 (brief justification)\n"
        f"{{2-3 sentence analysis}}"
    )


async def _analyze_dimension(
    dimension: dict, section_system: str, shared_context: str,
) -> tuple[str, str, str]:
    """Run one risk dimension's parallel analysis call, fully buffering both
    the thinking and text streams before returning. The caller in
    run_risk_analysis awaits all 6 of these tasks first, collecting their
    buffered output into a dimension_results dict WITHOUT emitting any SSE
    events yet; only after an optional per-dimension grading/fix-up pass
    (see _fix_weak_dimensions) does it loop over RISK_DIMENSIONS in
    canonical order and emit each dimension's final thinking/token events.
    This two-phase collect-then-emit shape is required because a per-
    dimension revision must be fully decided before its content is ever
    streamed to the frontend (the frontend has no per-dimension buffering,
    unlike report_generator.py's post-hoc whole-document revision).

    Returns (dimension_key, thinking_text, content_text).

    On any exception (API error, timeout, malformed stream, ...) logs a
    warning and returns a short English placeholder for content with empty
    thinking, instead of propagating — this task runs concurrently with 5
    siblings via asyncio.create_task, and one dimension failing must not
    sink the other five or the section as a whole.
    """
    prompt = _build_dimension_prompt(dimension)
    thinking_text = ""
    content_text = ""
    try:
        async for kind, token in stream_text_with_thinking(
            prompt, system=section_system, cached_context=shared_context,
            usage_log_tag="risk-dimension",
        ):
            if kind == "thinking":
                thinking_text += token
            else:
                content_text += token
    except Exception:
        logger.warning(
            "Risk dimension analysis failed for dimension=%r — using placeholder",
            dimension["key"],
            exc_info=True,
        )
        return (
            dimension["key"],
            "",
            f"### {dimension['title']}\n"
            f"*(Analysis unavailable — this dimension could not be evaluated.)*",
        )
    return (dimension["key"], thinking_text, content_text)


def _build_dimension_revision_prompt(
    dimension: dict, previous_content: str, unsupported_claims: list[str], additional_sources: str,
) -> str:
    """Pure function: build the prompt asking the model to rewrite ONE risk
    dimension's analysis (not the whole document) after a grader flagged
    specific unsupported claims in it, given newly-found source material
    aimed specifically at that dimension's gaps.

    Mirrors report_quality.py's build_revision_prompt in spirit (same
    "fix or soften the flagged claims" instruction, same XML-tagged-sections
    convention) but is dimension-scoped rather than whole-document-scoped,
    and — unlike that function, which only asks the model to fix wording
    against material it already had — injects genuinely NEW source material
    found for this purpose, which is the point of this feature.

    Requests the exact output format _build_dimension_prompt requires
    (### {title}\\nScore: X/10 (brief justification)\\n{2-3 sentence
    analysis}, no preamble) so the revised block still fits into
    section_content/full_content the same way the original did.

    No I/O, no randomness — safe to unit test directly.
    """
    if unsupported_claims:
        claims_block = "\n".join(f"- {claim}" for claim in unsupported_claims)
    else:
        claims_block = "(No specific claims were listed, but the analysis scored poorly on grounding.)"
    return (
        f"You previously wrote the analysis below for ONE risk dimension of a larger risk "
        f"assessment. A fact-checking reviewer compared it against the available source "
        f"material and flagged specific unsupported claims. New source material has since "
        f"been found specifically to address this dimension's gaps.\n\n"
        f"Dimension: **{dimension['title']}** {dimension['scale']}\n\n"
        f"<previous_analysis>\n{previous_content}\n</previous_analysis>\n\n"
        f"<unsupported_claims>\n{claims_block}\n</unsupported_claims>\n\n"
        f"<new_source_material>\n{additional_sources}\n</new_source_material>\n\n"
        f"Rewrite ONLY this dimension's analysis. Ground the flagged claims in the new "
        f"source material where it supports them; otherwise soften or remove them. Do not "
        f"discuss, list, or mention any other risk dimension.\n\n"
        f"Respond in exactly this format, with no preamble and no other headers:\n"
        f"### {dimension['title']}\n"
        f"Score: X/10 (brief justification)\n"
        f"{{2-3 sentence analysis}}"
    )


async def _grade_dimension_safe(content: str, source_material: str) -> dict | None:
    """Wrap verify_grounding() for one dimension's content in try/except,
    returning None on failure instead of propagating — matches the
    graceful-degradation style used at every other grading call site in this
    codebase (e.g. run_risk_analysis's final whole-document check)."""
    try:
        return await verify_grounding(content, source_material)
    except Exception:
        logger.warning(
            "Per-dimension grounding check failed — continuing without it", exc_info=True,
        )
        return None


async def _fix_weak_dimensions(
    dimension_results: dict[str, tuple[str, str]],
    subject: str,
    source_material: str,
    section_system: str,
    shared_context: str,
) -> dict[str, tuple[str, str]]:
    """Bounded evaluator-optimizer loop over the 6 risk_dimensions' individual
    grounding grades (see WEAK_DIMENSION_THRESHOLD / MAX_DIMENSIONS_TO_FIX).

    Grades all 6 dimensions in parallel, then — for up to
    MAX_DIMENSIONS_TO_FIX of the weakest-scoring dimensions below
    WEAK_DIMENSION_THRESHOLD — runs one bounded extra-research pass each: a
    targeted Tavily search, one regeneration grounded in the new material,
    and one re-grade. A revision is kept only if it scores at least as well
    as the original (report_quality.py's exact acceptance criterion,
    `>=`, accepting ties).

    Any failure at any step for a given dimension (search error, empty
    results, generation error, grading error) is logged as a warning and
    that dimension is simply skipped, keeping its original content — one
    dimension's failure never blocks the others or raises out of this
    function.

    Returns dimension_results, with 0 or more entries replaced in place by
    accepted revisions. Callers should treat the return value as the
    complete, final set of (thinking_text, content_text) pairs to emit.
    """
    grade_tasks = {
        key: asyncio.create_task(_grade_dimension_safe(content_text, source_material))
        for key, (_, content_text) in dimension_results.items()
    }
    grades: dict[str, dict | None] = {
        key: await task for key, task in grade_tasks.items()
    }

    def _score(key: str) -> float:
        grade = grades.get(key)
        if not grade:
            return 10
        return grade.get("confidence_score", 10)

    weak_keys = [
        key for key, grade in grades.items()
        if grade is not None and grade.get("confidence_score", 10) < WEAK_DIMENSION_THRESHOLD
    ]
    weak_keys.sort(key=_score)
    weak_keys = weak_keys[:MAX_DIMENSIONS_TO_FIX]

    if not weak_keys:
        return dimension_results

    dimensions_by_key = {dim["key"]: dim for dim in RISK_DIMENSIONS}

    for key in weak_keys:
        dimension = dimensions_by_key.get(key)
        if not dimension:
            continue
        grade = grades[key]
        original_content = dimension_results[key][1]

        query = f"{dimension['title']} {subject}"
        unsupported_claims = grade.get("unsupported_claims") or []
        if unsupported_claims:
            query += f" {unsupported_claims[0]}"

        try:
            results = await TavilyClient().search(query, max_results=3, search_depth="advanced")
        except Exception:
            logger.warning(
                "Extra-research Tavily search failed for weak dimension=%r — keeping original",
                key, exc_info=True,
            )
            continue

        source_blocks = []
        for r in results:
            body = (r.content or r.snippet or "")[:2000]
            source_blocks.append(f"{r.title} ({r.url})\n{body}")
        additional_sources = "\n\n".join(source_blocks)

        if not additional_sources:
            logger.warning(
                "Extra-research Tavily search returned no usable results for weak "
                "dimension=%r — keeping original", key,
            )
            continue

        revision_prompt = _build_dimension_revision_prompt(
            dimension, original_content, unsupported_claims, additional_sources,
        )

        try:
            revised_thinking = ""
            revised_content = ""
            async for kind, token in stream_text_with_thinking(
                revision_prompt, system=section_system, cached_context=shared_context,
                usage_log_tag="risk-dimension-revision",
            ):
                if kind == "thinking":
                    revised_thinking += token
                else:
                    revised_content += token
        except Exception:
            logger.warning(
                "Extra-research revision generation failed for weak dimension=%r — "
                "keeping original", key, exc_info=True,
            )
            continue

        # New material goes FIRST in combined_material: verify_grounding /
        # citation_verifier.py truncates source_material at 8000 chars from
        # the start, and if the original source_material is already near/
        # over that length, appending new material after it would get
        # truncated away entirely, defeating the point of the extra search.
        combined_material = additional_sources + "\n\n" + source_material

        try:
            regrade = await verify_grounding(revised_content, combined_material)
        except Exception:
            logger.warning(
                "Extra-research re-grade failed for weak dimension=%r — keeping original",
                key, exc_info=True,
            )
            continue

        if regrade.get("confidence_score", 0) >= grade.get("confidence_score", 0):
            dimension_results[key] = (revised_thinking, revised_content)

    return dimension_results


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

        # temperature=0.3 (logical/reproducible output for risk analysis) is no
        # longer passed here — adaptive thinking rejects a non-default
        # temperature (400 from the API). Thinking is on by default now, which
        # gives its own consistency benefit for this reasoning-heavy section.
        # System guard: research_material is untrusted — treat it as data only.
        section_system = template["system"] + "\n\n" + UNTRUSTED_CONTENT_GUARD

        section_content = ""
        if section_key == "risk_dimensions":
            # Parallel path: 6 independent per-dimension calls (see
            # _build_dimension_prompt / _analyze_dimension) instead of one
            # call cramming all 6 dimensions into a single prompt.
            #
            # Cache-ordering note: this launch happens after the
            # subject_profile section above has already made a
            # stream_text_with_thinking call against this same
            # shared_context, so the ephemeral cache_control write on
            # shared_context is already warm by the time these 6 tasks
            # start — they land as cache READS, not competing cache
            # writes. Moving this section earlier in the loop (or removing
            # subject_profile) would have all 6 tasks race to write the
            # same cache entry instead of reading an existing one.
            yield sse_event("status", {"message": "Analyzing 6 risk dimensions in parallel..."})

            tasks = [
                asyncio.create_task(_analyze_dimension(dimension, section_system, shared_context))
                for dimension in RISK_DIMENSIONS
            ]
            # Phase 1: await all 6 tasks first, collecting their buffered
            # output into dimension_results WITHOUT emitting any SSE events
            # yet. The tasks above are already scheduled and running
            # concurrently via asyncio.create_task; this gather only
            # determines when results become available, not when the
            # underlying work happens.
            results = await asyncio.gather(*tasks)
            dimension_results: dict[str, tuple[str, str]] = {
                key: (thinking_text, content_text) for key, thinking_text, content_text in results
            }

            # Phase 2: per-dimension weak-grounding re-research pass (see
            # _fix_weak_dimensions). Only reachable when there's source
            # material to grade against — same guard as the final
            # whole-document verify_grounding call below. May replace some
            # dimension_results entries in place with accepted revisions.
            # This MUST happen before any thinking/token events are emitted
            # for this section: the frontend (analysis/page.tsx) accumulates
            # every token event into one flat string with no per-dimension
            # buffering/replacement, so a per-dimension revision has to be
            # fully decided before its content is ever streamed.
            if source_material:
                yield sse_event("status", {"message": "Verifying dimension grounding..."})
                try:
                    dimension_results = await _fix_weak_dimensions(
                        dimension_results, request.subject, source_material,
                        section_system, shared_context,
                    )
                except Exception:
                    logger.warning(
                        "Per-dimension weak-grounding fix-up failed for %r — "
                        "continuing with original dimension content",
                        request.subject, exc_info=True,
                    )

            # Phase 3: emit thinking/token events in RISK_DIMENSIONS
            # (canonical) order, reading from dimension_results — which now
            # holds whichever content (original or accepted revision) won.
            for i, dimension in enumerate(RISK_DIMENSIONS):
                thinking_text, content_text = dimension_results[dimension["key"]]
                if thinking_text:
                    yield sse_event("thinking", {"text": thinking_text, "section": section_key})
                # Separate consecutive dimension blocks with "\n\n" in both
                # the emitted token and the accumulated section_content, so
                # the assembled markdown matches what was streamed.
                block = content_text if i == 0 else "\n\n" + content_text
                section_content += block
                yield sse_event("token", {"text": block, "section": section_key})
        else:
            prompt = _build_section_prompt(section_title, section_def["instructions"])
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
