"""Evaluator-optimizer feedback loop for report generation.

report_generator.py already runs a single grading pass after a report is
written: services/citation_verifier.py's verify_grounding() (an LLM-as-judge
call) checks whether the generated content is actually supported by its
source material and returns {confidence_score, unsupported_claims, notes}.
That alone is a grader with no feedback loop — flagged claims are surfaced to
the user but nothing acts on them.

This module closes the loop: if the grader flags unsupported claims, ask the
model to produce a corrected report that fixes or removes ONLY those claims,
then re-grade the result and keep whichever version scores at least as well
as the original. This is deliberately bounded to a single revision attempt
(see revise_if_ungrounded) rather than an open-ended critique/revise loop —
each unsupported-claims finding costs at most one extra generation call plus
one extra grading call, so total cost for a report is at most 2x the base
grading cost regardless of how the revision turns out.
"""
import logging

from services.anthropic_client import stream_text_with_thinking
from services.citation_verifier import verify_grounding

logger = logging.getLogger(__name__)

# Output budget for report generation and revision calls. The old implicit
# default was stream_text_with_thinking's max_tokens=8192, which is SHARED
# with adaptive-thinking tokens — an 8-section congressional brief, or a
# whole-report rewrite, routinely ran out of it and got cut mid-sentence.
#
# Ceiling check via the free Models API (2026-09-03), over
# model_catalog.FALLBACK_ANTHROPIC_MODELS — the ids this app can be pointed at:
#   claude-fable-5              max_tokens 128000
#   claude-opus-5               max_tokens 128000
#   claude-sonnet-5             max_tokens 128000
#   claude-haiku-4-5-20251001   max_tokens  64000
# Minimum cap is 64000, so 32000 is comfortably below every allowed model's
# limit (the API 400s on a max_tokens above the model's cap).
#
# Lives here rather than in report_generator.py because report_generator
# imports this module — the reverse import would be circular.
REPORT_MAX_TOKENS = 32000


def build_revision_prompt(full_content: str, unsupported_claims: list[str], notes: str) -> str:
    """Pure function: build the prompt asking the model to correct a report it
    already wrote, given a fact-checking review that flagged specific
    unsupported claims.

    Instructs the model to touch ONLY the flagged claims — ground each one in
    the source material if possible, otherwise delete or soften it — and to
    leave everything else, including the full heading structure (the leading
    `# Report Title` line and every `## Section` heading), untouched. Wraps
    the claims list and the previous report in descriptive XML tags, matching
    this codebase's convention (see citation_verifier.py's <source_material>/
    <generated_content> tags).

    No I/O, no randomness — safe to unit test directly (see
    tests/test_report_revision.py).
    """
    claims_block = "\n".join(f"- {claim}" for claim in unsupported_claims)
    notes_block = f"<review_notes>\n{notes}\n</review_notes>\n\n" if notes else ""
    return (
        "You previously wrote the report below. A fact-checking reviewer compared it against "
        "the original source material and flagged the specific claims listed below as "
        "unsupported — fabricated, unverifiable from the source, or contradicting the source.\n\n"
        f"<unsupported_claims>\n{claims_block}\n</unsupported_claims>\n\n"
        f"{notes_block}"
        f"<previous_report>\n{full_content}\n</previous_report>\n\n"
        "Produce the complete corrected report. Fix or remove ONLY the flagged claims above: "
        "ground each one in the source material if it supports a corrected version, otherwise "
        "delete it or soften it into a properly hedged, general statement. Leave every other "
        "sentence exactly as it was — do not paraphrase, reorder, or otherwise rewrite unflagged "
        "content. Preserve the full document structure verbatim, including the leading "
        "'# Report Title' line and every '## Section' heading exactly as they appear in "
        "<previous_report>.\n\n"
        "Output ONLY the corrected report in Markdown — no preamble, no explanation, no "
        "meta-commentary about what changed."
    )


async def revise_if_ungrounded(
    full_content: str,
    source_material: str,
    first_grade: dict | None,
    *,
    system_prompt: str,
    cached_context: str | None,
    usage_log_tag: str | None,
):
    """Bounded evaluator-optimizer loop over a single verify_grounding() grade.

    If `first_grade` has no unsupported claims (None, missing key, or an
    empty list), no revision is attempted — yields only the final sentinel
    with the original content.

    Otherwise: streams one revision attempt (see build_revision_prompt),
    re-grades the revised content with verify_grounding, and accepts the
    revision only if its confidence_score is >= the first grade's
    confidence_score (the evaluator's acceptance criterion) — otherwise the
    original content + first grade are kept. Exactly one revision iteration
    is attempted (hard-coded, not a loop) to bound the extra cost of this
    feedback loop to one generation call + one grading call per report, no
    matter how the grader responds.

    One exception overrides that acceptance criterion: if the revision stream
    ends with the ("truncated", "") sentinel — the response hit the model's
    max_tokens output cap — the revised text is a fragment, so it is neither
    re-graded nor adopted. The score comparison actively misleads here: a
    fragment contains fewer claims, so the re-grade tends to score it HIGHER
    than the full original, which is how a 311-word stub once replaced a
    complete 8-section brief in production. The revision call therefore also
    asks for REPORT_MAX_TOKENS rather than the 8192 default.

    Any exception raised while streaming the revision or re-grading it is
    caught, logged as a warning, and treated as "keep the original" —
    matching the graceful-degradation try/except style already used around
    verify_grounding in report_generator.py. The final sentinel is always
    yielded, even on failure, so callers can rely on always seeing exactly
    one "final" event.

    Yields (kind, payload) tuples in this order:
      ("revision_start", {"unsupported_claims": [...], "confidence_score": ...})
        — only when a revision is attempted.
      ("token", str) / ("thinking", str) — streamed revision tokens, in
        stream order, only when a revision is attempted.
      ("revision_end", {"accepted": bool, "confidence_score": ...})
        — only when a revision is attempted; confidence_score is the
        re-graded score if accepted, else the first grade's score. Carries an
        extra "truncated": True key only when the revision was cut off at
        max_tokens (the frontend ignores keys it doesn't read).
      ("final", {"content": str, "grade": dict | None, "revised": bool})
        — always yielded exactly once, last. `content`/`grade` are whichever
        version was chosen (revised or original).
    """
    if not first_grade or not first_grade.get("unsupported_claims"):
        yield ("final", {"content": full_content, "grade": first_grade, "revised": False})
        return

    unsupported_claims = first_grade["unsupported_claims"]
    first_score = first_grade.get("confidence_score", 0)

    yield ("revision_start", {
        "unsupported_claims": unsupported_claims,
        "confidence_score": first_grade.get("confidence_score"),
    })

    chosen_content = full_content
    chosen_grade = first_grade
    accepted = False
    truncated = False

    try:
        prompt = build_revision_prompt(full_content, unsupported_claims, first_grade.get("notes", ""))
        revised_content = ""
        async for kind, token in stream_text_with_thinking(
            prompt, system=system_prompt, cached_context=cached_context,
            usage_log_tag=usage_log_tag, max_tokens=REPORT_MAX_TOKENS,
        ):
            if kind == "truncated":
                # Control signal, not content — never accumulated, never
                # forwarded as a token.
                truncated = True
                continue
            if kind == "thinking":
                yield ("thinking", token)
                continue
            revised_content += token
            yield ("token", token)

        if truncated:
            # Do not re-grade: the score of a fragment is not comparable to
            # the score of the whole report, and it skews HIGH (fewer claims
            # left to flag), so the usual >= test would adopt the fragment.
            logger.warning("Report revision hit max_tokens — keeping pre-revision content")
            accepted = False
        else:
            regraded = await verify_grounding(revised_content, source_material)
            if regraded.get("confidence_score", 0) >= first_score:
                chosen_content = revised_content
                chosen_grade = regraded
                accepted = True
    except Exception:
        logger.warning(
            "Report revision failed — keeping pre-revision content", exc_info=True,
        )
        chosen_content = full_content
        chosen_grade = first_grade
        accepted = False

    revision_end_payload = {
        "accepted": accepted,
        "confidence_score": chosen_grade.get("confidence_score") if chosen_grade else None,
    }
    if truncated:
        revision_end_payload["truncated"] = True
    yield ("revision_end", revision_end_payload)
    yield ("final", {"content": chosen_content, "grade": chosen_grade, "revised": accepted})
