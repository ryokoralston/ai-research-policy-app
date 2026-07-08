"""Citation / grounding verification for high-stakes generated content.

After Risk Analysis or Report generation finishes writing a document, an
additional cheap LLM-as-judge call checks whether the generated content is
actually supported by its source material. This surfaces a confidence score
and any unsupported claims to the user, reducing ungrounded claims reaching
congressional briefs, policy memos, and risk assessments.

The rubric is calibrated for professional analytical writing: these documents
are written by a senior analyst who is expected to apply domain expertise,
reasoned judgment, and standard analytical frameworks on top of the source
material — not merely restate it verbatim. The judge is instructed to flag
CONCERNING content only (fabricated statistics, contradictions, unsupported
specific facts), not normal analytical elaboration, inference, or the
structural/scoring elements the generator was asked to produce.
"""
from services.anthropic_client import generate_json

# Truncation limits match the source_material[:6000]/[:8000] limits already
# used for prompt construction elsewhere in risk_analyzer.py / report_generator.py.
_SOURCE_MATERIAL_TRUNCATE = 8000
_GENERATED_CONTENT_TRUNCATE = 8000


async def verify_grounding(content: str, source_material: str) -> dict:
    """Check whether `content` is grounded in `source_material`.

    Returns a dict with exactly these keys:
      confidence_score: number 0-10, where 8-10 = well-grounded (reasonable
        analytical elaboration is expected and fine), 5-7 = a few specific
        unsupported factual claims but overall sound, 0-4 = fabricated
        statistics or claims contradicting the source
      unsupported_claims: list of short strings, each describing one specific
        fabricated fact/statistic/contradiction — not general analytical
        statements (empty list if none found)
      notes: one-sentence summary string

    Uses generate_json() with no model override, so this runs on the fast/
    cheap model by default (temperature=0.0) — same cost tier as
    risk_analyzer.py's existing score-extraction call
    (`extracted_scores = await generate_json(scores_prompt, temperature=0.0)`).

    No system prompt / UNTRUSTED_CONTENT_GUARD is applied here. This mirrors
    risk_analyzer.py's own scores_prompt call site, which is the closest
    analogous internal-structured-extraction call in this codebase and also
    calls generate_json() with no system prompt — matching that established
    precedent rather than inventing a new convention for this call.

    Does not catch exceptions — they propagate to the caller, matching
    generate_json's own documented behavior ("Exceptions propagate to the
    caller — call sites keep their own fallback behavior"). Callers are
    responsible for their own try/except and graceful degradation.
    """
    prompt = (
        "You are reviewing a professional analytical document (e.g. an AI risk assessment or "
        "policy report) written by a senior analyst who is expected to apply domain expertise, "
        "reasoned judgment, and standard analytical frameworks — not merely restate the source "
        "material verbatim. Your job is to flag CONCERNING content, not normal analytical writing.\n\n"
        "Do NOT flag as unsupported:\n"
        "- General domain knowledge, reasonable inference, or expert judgment that is consistent "
        "with (even if not verbatim stated in) the source material\n"
        "- Structural/framework elements the writer was instructed to produce, such as numeric "
        "risk scores, timeframe classifications (near/medium/long-term), or qualitative severity "
        "ratings, when they follow reasonably from the source's content\n"
        "- Standard contextual framing (e.g. widely known facts about an actor, technology, or "
        "policy area) that a subject-matter expert would know\n\n"
        "DO flag as unsupported:\n"
        "- Specific factual claims (named events, dates, quotes, incidents, named individuals) "
        "that are not in the source material and are not common domain knowledge\n"
        "- Specific numbers/statistics presented as if empirically sourced (e.g. '73% of...', "
        "'$2.4 million in damages') that do not appear in and cannot be reasonably derived from "
        "the source material\n"
        "- Claims that directly contradict the source material\n\n"
        f"<source_material>\n{source_material[:_SOURCE_MATERIAL_TRUNCATE]}\n</source_material>\n\n"
        f"<generated_content>\n{content[:_GENERATED_CONTENT_TRUNCATE]}\n</generated_content>\n\n"
        "Return a JSON object with exactly these keys:\n"
        "- confidence_score: 0-10. 8-10 = well-grounded (reasonable analytical elaboration is "
        "expected and fine); 5-7 = a few specific unsupported factual claims but overall sound; "
        "0-4 = fabricated statistics or claims contradicting the source.\n"
        "- unsupported_claims: array of short strings, each describing ONE specific fabricated "
        "fact/statistic/contradiction (not general analytical statements). Empty array if none.\n"
        "- notes: one-sentence summary of your assessment."
    )
    return await generate_json(prompt, temperature=0.0)
