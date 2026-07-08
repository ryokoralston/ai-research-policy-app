"""Consensus Meter extraction for the Multi-Persona Debate feature.

After a debate's 4 rounds + moderator synthesis finish, one additional cheap
LLM call identifies the 3-5 key claims/positions that were actually debated
and classifies each persona's real stance on each as agree/disagree/mixed.
This powers the frontend's compact "Consensus Meter" visual so users get an
at-a-glance view of where debate participants actually converged or diverged,
instead of having to re-read the whole transcript.

Mirrors services/citation_verifier.py's conventions: generate_json() with no
model override (runs on the fast/cheap model by default), no system prompt,
temperature=0.0, and no try/except here — exceptions propagate to the caller
(debate_service.py's run_debate), which owns its own graceful degradation.
"""
from services.anthropic_client import generate_json
from services.debate_service import _format_history
from templates.personas import PERSONAS

# No existing truncation limit covers a full debate transcript specifically,
# so this reuses the same 8000-char limit citation_verifier.py already uses
# for source_material/generated_content, for consistency with this codebase's
# other truncation constants rather than inventing a new one.
_TRANSCRIPT_TRUNCATE = 8000


async def extract_consensus(history: list[dict], synthesis: str, persona_keys: list[str]) -> dict:
    """Identify 3-5 contested claims and each listed persona's real stance on each.

    Returns a dict shaped:
      {"claims": [{"claim": "short label, under 12 words",
                   "stances": {persona_key: "agree"|"disagree"|"mixed", ...}},
                  ...]}

    Uses generate_json() with no model override (fast/cheap model) and no
    system prompt, matching citation_verifier.py's verify_grounding() call
    site precedent. Does not catch exceptions — they propagate to the caller,
    same as verify_grounding()'s documented behavior.
    """
    transcript_text = _format_history(history)[:_TRANSCRIPT_TRUNCATE]

    persona_list = "\n".join(
        f"- {key} ({PERSONAS[key]['name']})" for key in persona_keys if key in PERSONAS
    )

    prompt = (
        "You are analyzing the transcript of a multi-persona policy debate to build a "
        "\"Consensus Meter\" — a compact summary of where the debate participants actually "
        "agreed or disagreed.\n\n"
        "The personas who participated in this debate are exactly these (use these exact "
        "persona keys in your output — do not invent new ones and do not omit any):\n"
        f"{persona_list}\n\n"
        f"<debate_transcript>\n{transcript_text}\n</debate_transcript>\n\n"
        f"<synthesis>\n{synthesis}\n</synthesis>\n\n"
        "Identify 3-5 major claims or positions that were genuinely contested or discussed "
        "in this debate (only claims actually raised in the transcript — do not invent "
        "hypothetical ones). For each claim, classify EVERY listed persona's actual stance "
        "based on what they actually argued in the transcript — not what you'd guess from "
        "their general reputation or stereotype. Each stance must be exactly one of "
        "\"agree\", \"disagree\", or \"mixed\". If a persona never addressed a particular "
        "claim at all, classify their stance as \"mixed\" — there is no \"not addressed\" "
        "option, so \"mixed\" is the neutral fallback for that case.\n\n"
        "Return a JSON object with exactly this shape:\n"
        "{\"claims\": [{\"claim\": \"short label, under 12 words\", "
        "\"stances\": {\"<persona_key>\": \"agree\"|\"disagree\"|\"mixed\", ...}}, ...]}\n\n"
        "Every persona key listed above must appear in every claim's stances object."
    )

    return await generate_json(prompt, temperature=0.0)
