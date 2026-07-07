"""Citation / grounding verification for high-stakes generated content.

After Risk Analysis or Report generation finishes writing a document, an
additional cheap LLM-as-judge call checks whether the generated content is
actually supported by its source material. This surfaces a confidence score
and any unsupported claims to the user, reducing ungrounded claims reaching
congressional briefs, policy memos, and risk assessments.
"""
from services.anthropic_client import generate_json

# Truncation limits match the source_material[:6000]/[:8000] limits already
# used for prompt construction elsewhere in risk_analyzer.py / report_generator.py.
_SOURCE_MATERIAL_TRUNCATE = 8000
_GENERATED_CONTENT_TRUNCATE = 8000


async def verify_grounding(content: str, source_material: str) -> dict:
    """Check whether `content` is grounded in `source_material`.

    Returns a dict with exactly these keys:
      confidence_score: number 0-10, where 10 = fully grounded
      unsupported_claims: list of short strings (empty list if none found)
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
        "Compare the generated content below against its source material. "
        "Identify any claims, statistics, or assertions in the generated "
        "content that are NOT supported by the source material.\n\n"
        f"<source_material>\n{source_material[:_SOURCE_MATERIAL_TRUNCATE]}\n</source_material>\n\n"
        f"<generated_content>\n{content[:_GENERATED_CONTENT_TRUNCATE]}\n</generated_content>\n\n"
        "Return a JSON object with exactly these keys:\n"
        "- confidence_score: a number from 0-10, where 10 means the generated "
        "content is fully grounded in the source material and 0 means none of "
        "it is supported.\n"
        "- unsupported_claims: an array of short strings, each describing one "
        "claim in the generated content that is not supported by the source "
        "material. Return an empty array if there are none.\n"
        "- notes: a one-sentence summary of your assessment."
    )
    return await generate_json(prompt, temperature=0.0)
