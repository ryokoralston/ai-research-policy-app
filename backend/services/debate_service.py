"""
Debate orchestration service.
Runs a 4-round structured debate among AI policy personas, streaming SSE events.
"""
import asyncio
import uuid
from datetime import datetime

from services.anthropic_client import stream_text, sse_event
from templates.personas import PERSONAS, MODERATOR_SYSTEM, ROUNDS


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
        f"You have just moderated a four-round policy debate on the following topic:\n\n"
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


async def run_debate(
    debate_id: str,
    topic: str,
    persona_keys: list[str],
    queue: asyncio.Queue,
) -> None:
    """
    Main debate orchestration coroutine.
    Runs 4 rounds sequentially, then synthesis. Pushes SSE events to queue.
    """
    from models.debate import Debate, DebateArgument
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

        # Save synthesis and mark complete
        debate = db.query(Debate).filter(Debate.id == debate_id).first()
        if debate:
            debate.synthesis = synthesis
            debate.status = "complete"
            debate.completed_at = datetime.utcnow()
            db.commit()

        await queue.put(sse_event("complete", {"debate_id": debate_id, "event_type": "complete"}))

    except Exception as e:
        import json
        await queue.put(f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n")
        debate = db.query(Debate).filter(Debate.id == debate_id).first()
        if debate:
            debate.status = "error"
            db.commit()
    finally:
        db.close()
