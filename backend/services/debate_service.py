"""
Debate orchestration service.
Runs a 4-round structured debate among AI policy personas, streaming SSE events.

If the debate is still genuinely divided after those 4 fixed rounds (see
CONSENSUS_DIVERGENCE_THRESHOLD / _consensus_divergence_score below), one
additional bounded round targeting the single most contested claim runs,
followed by a re-synthesis and a re-extraction of the Consensus Meter. This
is the same evaluator-optimizer shape as report_quality.py's
revise_if_ungrounded (grade → one bounded corrective pass → re-grade), not
research_agent.py's multi-iteration MAX_GAP_ITERATIONS loop — an extra round
here costs one full LLM call per persona, so it is capped at exactly one.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime

from services.anthropic_client import stream_text, sse_event
from templates.personas import PERSONAS, MODERATOR_SYSTEM, ROUNDS

logger = logging.getLogger(__name__)

# Fraction of extracted claims that must show a genuine agree/disagree split
# (both stances actually present among participants — NOT counting "mixed",
# since consensus_meter.py's own docstring says "mixed" is the neutral
# fallback for personas that never addressed a claim, not evidence of real
# disagreement) to trigger the one bounded extra round. Cost-bound rationale:
# matches report_quality.py's single-revision-attempt precedent — an extra
# round costs one full persona-count of LLM calls, so this must stay capped
# at exactly one extra round, not become an open-ended loop (contrast with
# research_agent.py's MAX_GAP_ITERATIONS, which is a genuinely different
# cost/benefit tradeoff for that feature).
CONSENSUS_DIVERGENCE_THRESHOLD = 0.5


def _consensus_divergence_score(claims: list[dict]) -> float:
    """Pure function: fraction (0.0-1.0) of `claims` where at least one
    persona's stance is "agree" and at least one (other) persona's stance is
    "disagree" on that same claim. "mixed" stances are ignored — they are the
    neutral fallback consensus_meter.py assigns to personas that never
    addressed a claim, not evidence of real disagreement.

    Returns 0.0 for an empty claims list. No I/O — unit-testable directly.
    """
    if not claims:
        return 0.0

    split_count = 0
    for claim in claims:
        stances = claim.get("stances", {}) or {}
        values = stances.values()
        if "agree" in values and "disagree" in values:
            split_count += 1

    return split_count / len(claims)


def _select_most_contested_claim(claims: list[dict]) -> dict | None:
    """Pure function: among `claims` with both an "agree" and a "disagree"
    stance present, return the one with the most EVEN split — i.e. the claim
    that maximizes min(agree_count, disagree_count) across its stances. That
    is the most genuinely (rather than lopsidedly) contested claim.

    Returns None if no claim qualifies. Defensive: this is only meant to be
    called after _consensus_divergence_score has already confirmed the
    threshold is met, but must not crash on an empty/non-qualifying list.
    """
    best_claim: dict | None = None
    best_min_count = -1

    for claim in claims:
        stances = claim.get("stances", {}) or {}
        values = list(stances.values())
        agree_count = values.count("agree")
        disagree_count = values.count("disagree")
        if agree_count > 0 and disagree_count > 0:
            evenness = min(agree_count, disagree_count)
            if evenness > best_min_count:
                best_min_count = evenness
                best_claim = claim

    return best_claim


def _build_extra_round_instructions(claim_text: str) -> str:
    """Pure function: build the round-5 instructions targeting the single
    most contested claim, so the extra round asks personas to engage that
    specific disagreement directly instead of re-litigating the whole topic.
    """
    return (
        f'The debate remains genuinely split on this specific claim: "{claim_text}". '
        "Directly explain why you hold your position on it, and respond to the "
        "strongest opposing view you've heard on this specific point. 80-100 words."
    )


def _format_history(history: list[dict]) -> str:
    """Format debate history for inclusion in persona prompts."""
    if not history:
        return "(No previous arguments yet — you are the first to speak.)"
    lines = []
    for entry in history:
        lines.append(f"[{entry['round_name']}] {entry['persona_name']}: {entry['content']}")
        lines.append("")
    return "\n".join(lines)


def _build_persona_prompt(
    persona: dict,
    topic: str,
    round_name: str,
    round_instructions: str,
    history: list[dict],
) -> str:
    history_text = _format_history(history)
    return (
        f"Policy Topic: {topic}\n\n"
        f"Round: {round_name}\n"
        f"Your Task: {round_instructions}\n\n"
        f"=== DEBATE HISTORY ===\n"
        f"{history_text}\n"
        f"=====================\n\n"
        f"Now respond as {persona['name']} ({persona['title']}). "
        f"Be direct, specific, and stay in character. "
        f"Do NOT introduce yourself or explain who you are. Just argue your position."
    )


def _build_synthesis_prompt(history: list[dict], topic: str) -> str:
    history_text = _format_history(history)
    return (
        f"You have just moderated a multi-round policy debate on the following topic:\n\n"
        f"TOPIC: {topic}\n\n"
        f"Here is the complete transcript:\n\n"
        f"=== DEBATE TRANSCRIPT ===\n"
        f"{history_text}\n"
        f"========================\n\n"
        f"Write a synthesis of 300-400 words covering:\n"
        f"1. Areas of genuine agreement across participants\n"
        f"2. The most fundamental irreconcilable splits and why they exist\n"
        f"3. The top 3 policy priorities that emerged from the debate\n\n"
        f"Be precise about which participants held which views. "
        f"Do not add new arguments—synthesize what was actually said."
    )


async def _run_round(
    round_num: int,
    round_name: str,
    round_instructions: str,
    topic: str,
    persona_keys: list[str],
    history: list[dict],
    debate_id: str,
    db,
    queue: asyncio.Queue,
    order_index: int,
) -> int:
    """Run one debate round: every persona in `persona_keys` gets prompted in
    turn, streams a response, and has it saved as a DebateArgument row and
    appended to `history` (mutated in place). Emits round_start, per-persona
    persona_start/token/persona_end, and round_end SSE events.

    Extracted out of run_debate's fixed-4-round loop so both the `for
    round_num, round_name, round_instructions in ROUNDS:` loop and the
    conditional 5th "extra round" (see CONSENSUS_DIVERGENCE_THRESHOLD) share
    identical round-running logic — no duplicated code between call sites.

    Returns the updated order_index (DebateArgument.order_index is
    monotonically increasing across the whole debate, not per-round).
    """
    from models.debate import DebateArgument

    await queue.put(sse_event("round_start", {"round": round_num, "round_name": round_name}))

    for persona_key in persona_keys:
        persona = PERSONAS[persona_key]
        await queue.put(sse_event("persona_start", {
            "persona_key": persona_key,
            "persona_name": persona["name"],
            "round": round_num,
        }))

        prompt = _build_persona_prompt(
            persona=persona,
            topic=topic,
            round_name=round_name,
            round_instructions=round_instructions,
            history=history,
        )

        content = ""
        async for token in stream_text(prompt, system=persona["system"]):
            content += token
            await queue.put(sse_event("token", {
                "text": token,
                "persona_key": persona_key,
                "round": round_num,
            }))

        # Save argument to DB immediately
        argument = DebateArgument(
            id=str(uuid.uuid4()),
            debate_id=debate_id,
            persona_key=persona_key,
            persona_name=persona["name"],
            round_number=round_num,
            round_name=round_name,
            content=content,
            order_index=order_index,
        )
        db.add(argument)
        db.commit()
        order_index += 1

        word_count = len(content.split())
        history.append({
            "persona_name": persona["name"],
            "round_name": round_name,
            "content": content,
        })

        await queue.put(sse_event("persona_end", {
            "persona_key": persona_key,
            "word_count": word_count,
        }))

    await queue.put(sse_event("round_end", {"round": round_num}))
    return order_index


async def run_debate(
    debate_id: str,
    topic: str,
    persona_keys: list[str],
    queue: asyncio.Queue,
) -> None:
    """
    Main debate orchestration coroutine.
    Runs 4 rounds sequentially, then synthesis. Pushes SSE events to queue.
    If the Consensus Meter shows genuine divergence, runs one bounded extra
    round + re-synthesis + re-extraction (see CONSENSUS_DIVERGENCE_THRESHOLD).
    """
    from models.debate import Debate
    from database import SessionLocal

    db = SessionLocal()
    try:
        debate = db.query(Debate).filter(Debate.id == debate_id).first()
        if not debate:
            return
        debate.status = "running"
        db.commit()

        history: list[dict] = []
        order_index = 0

        for round_num, round_name, round_instructions in ROUNDS:
            order_index = await _run_round(
                round_num, round_name, round_instructions,
                topic, persona_keys, history, debate_id, db, queue, order_index,
            )

        # Synthesis
        await queue.put(sse_event("synthesis_start", {}))
        synthesis_prompt = _build_synthesis_prompt(history, topic)
        synthesis = ""
        async for token in stream_text(synthesis_prompt, system=MODERATOR_SYSTEM):
            synthesis += token
            await queue.put(sse_event("token", {
                "text": token,
                "persona_key": "moderator",
                "round": 0,
            }))

        # Consensus Meter: one extra cheap LLM-as-judge call that identifies the
        # 3-5 claims actually contested in the debate and classifies each
        # persona's real stance (agree/disagree/mixed) on each. A failure
        # degrades gracefully (logged, continue without it) rather than
        # blocking the debate from completing — same style as
        # risk_analyzer.py's citation-verification failure handling.
        consensus: dict | None = None
        try:
            from services.consensus_meter import extract_consensus
            consensus = await extract_consensus(history, synthesis, persona_keys)
        except Exception:
            logger.warning(
                "Consensus extraction failed for debate %r — continuing without it",
                debate_id,
                exc_info=True,
            )

        extra_round_ran = False

        if consensus:
            await queue.put(sse_event("consensus", {"claims": consensus.get("claims", [])}))

            # Bounded evaluator-optimizer extension: if the debate is still
            # genuinely divided, run exactly ONE extra round targeted at the
            # single most contested claim, then re-synthesize and re-extract
            # consensus. See CONSENSUS_DIVERGENCE_THRESHOLD's docstring for
            # the cost-bound rationale (matches report_quality.py's
            # single-revision-attempt precedent).
            divergence_score = _consensus_divergence_score(consensus.get("claims", []))
            if divergence_score >= CONSENSUS_DIVERGENCE_THRESHOLD:
                contested_claim = _select_most_contested_claim(consensus.get("claims", []))
                if contested_claim is not None:
                    extra_round_ran = True
                    extra_instructions = _build_extra_round_instructions(
                        contested_claim.get("claim", "")
                    )
                    order_index = await _run_round(
                        5, "Addressing the Core Disagreement", extra_instructions,
                        topic, persona_keys, history, debate_id, db, queue, order_index,
                    )

                    # A brand-new synthesis is about to stream, superseding the
                    # first one — resynthesis_start lets the frontend
                    # distinguish this from "more tokens of the same
                    # synthesis" (mirrors research_agent.py Step 5's use of
                    # the same event for the same reason).
                    await queue.put(sse_event("resynthesis_start", {}))
                    resynthesis_prompt = _build_synthesis_prompt(history, topic)
                    new_synthesis = ""
                    async for token in stream_text(resynthesis_prompt, system=MODERATOR_SYSTEM):
                        new_synthesis += token
                        await queue.put(sse_event("token", {
                            "text": token,
                            "persona_key": "moderator",
                            "round": 0,
                        }))

                    try:
                        new_consensus = await extract_consensus(history, new_synthesis, persona_keys)
                        consensus = new_consensus
                        synthesis = new_synthesis
                        await queue.put(sse_event("consensus", {"claims": consensus.get("claims", [])}))
                    except Exception:
                        logger.warning(
                            "Extra-round consensus re-extraction failed for debate %r — "
                            "keeping first-pass synthesis and consensus",
                            debate_id,
                            exc_info=True,
                        )

        # Save synthesis, consensus, and mark complete
        debate = db.query(Debate).filter(Debate.id == debate_id).first()
        if debate:
            debate.synthesis = synthesis
            debate.consensus_json = json.dumps(consensus) if consensus else None
            debate.status = "complete"
            debate.completed_at = datetime.utcnow()
            db.commit()

        await queue.put(sse_event("complete", {
            "debate_id": debate_id,
            "event_type": "complete",
            "consensus": consensus,
            "extra_round": extra_round_ran,
        }))

    except Exception as e:
        await queue.put(sse_event("error", {"message": str(e)}))
        debate = db.query(Debate).filter(Debate.id == debate_id).first()
        if debate:
            debate.status = "error"
            db.commit()
    finally:
        db.close()
