"""
Research Agent: orchestrates web search + Claude synthesis.

Pipeline:
  1. Query decomposition (Claude generates sub-queries)
  2. Parallel Tavily searches
  3. Per-source summarization (claude-haiku-3-5)
  4. Final synthesis (claude-opus-4-6, streaming)
"""
import asyncio
import json
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from models import ResearchSession, SearchResult
from services.anthropic_client import generate_text, stream_text, sse_event
from services.tavily_client import TavilyClient, SearchResult as TavilyResult


async def run_research_agent(
    session_id: str,
    query: str,
    max_sources: int,
    queue: asyncio.Queue,
    db: Session,
) -> None:
    # ── Step 1: Query Decomposition ───────────────────────────────────────────
    await queue.put(sse_event("status", {"message": "Decomposing research query..."}))

    decomp_prompt = (
        f"You are a policy research assistant. Given this research question, "
        f"generate exactly 3 specific search queries that together provide comprehensive coverage.\n\n"
        f"Research question: {query}\n\n"
        f'Return ONLY a JSON array of 3 strings, like: ["query1", "query2", "query3"]'
    )
    try:
        # Pre-fill forces Claude to start with a JSON array open bracket.
        # Stop sequence cuts off generation once the array closes,
        # preventing any trailing explanation text.
        # Result: '```json\n["query1", "query2", "query3"]\n'
        decomp_raw = await generate_text(
            decomp_prompt,
            temperature=0.2,
            prefill="```json",
            stop_sequences=["```"],
        )
        # Strip the markdown fence prefix, then strip surrounding whitespace
        json_str = decomp_raw[len("```json"):].strip()
        sub_queries: list[str] = json.loads(json_str)
    except Exception:
        sub_queries = [query]

    await queue.put(sse_event("queries", {"queries": sub_queries}))

    # ── Step 2: Parallel Tavily Searches ──────────────────────────────────────
    await queue.put(sse_event("status", {"message": f"Searching {len(sub_queries)} queries..."}))

    tavily = TavilyClient()
    per_query_max = max(3, max_sources // len(sub_queries))
    search_tasks = [
        tavily.search(q, max_results=per_query_max, search_depth="advanced")
        for q in sub_queries
    ]
    results_nested = await asyncio.gather(*search_tasks, return_exceptions=True)

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique_results: list[TavilyResult] = []
    for batch in results_nested:
        if isinstance(batch, Exception):
            continue
        for r in batch:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                unique_results.append(r)

    # Sort by relevance score, cap at max_sources
    unique_results.sort(key=lambda r: r.score, reverse=True)
    unique_results = unique_results[:max_sources]

    await queue.put(sse_event("sources_found", {"count": len(unique_results)}))

    # ── Step 3: Per-Source Summarization ──────────────────────────────────────
    await queue.put(sse_event("status", {"message": "Summarizing sources..."}))

    db_results: list[SearchResult] = []
    summarized: list[dict] = []

    for order, result in enumerate(unique_results):
        content_for_summary = result.content or result.snippet or ""
        # Trim to avoid huge prompts
        if len(content_for_summary) > 6000:
            content_for_summary = content_for_summary[:6000] + "..."

        summary_prompt = (
            f"You are summarizing a web source for AI policy research.\n"
            f"Original query: {query}\n"
            f"Source: {result.title} ({result.url})\n"
            f"Content:\n{content_for_summary}\n\n"
            f"Provide:\n"
            f"1. A 2-3 sentence summary of the key information\n"
            f"2. Key claims (2-3 bullet points)\n"
            f"3. Relevance to the research query (1 sentence)\n\n"
            f"Be concise and factual."
        )

        try:
            # temperature=0.3: 事実の要約なので低め（再現性重視）
            ai_summary = await generate_text(summary_prompt, temperature=0.3)
        except Exception as e:
            ai_summary = result.snippet or ""

        db_result = SearchResult(
            id=str(uuid.uuid4()),
            session_id=session_id,
            url=result.url,
            title=result.title,
            snippet=result.snippet,
            full_content=(result.content or "")[:10000],  # store first 10k chars
            relevance_score=result.score,
            ai_summary=ai_summary,
            published_date=result.published_date,
            result_order=order,
        )
        db.add(db_result)
        db.commit()
        db_results.append(db_result)

        summarized.append({
            "order": order + 1,
            "title": result.title,
            "url": result.url,
            "summary": ai_summary,
            "score": result.score,
        })

        await queue.put(sse_event("source_processed", {
            "order": order + 1,
            "title": result.title,
            "url": result.url,
            "snippet": result.snippet,
            "ai_summary": ai_summary,
        }))

    # ── Step 4: Synthesis (streaming) ─────────────────────────────────────────
    await queue.put(sse_event("status", {"message": "Synthesizing findings..."}))

    sources_text = "\n\n".join(
        f"[Source {s['order']}] {s['title']} ({s['url']})\n{s['summary']}"
        for s in summarized
    )

    system = (
        "You are a senior AI policy analyst at a leading think tank. "
        "Your syntheses are used to brief members of Congress and senior policy officials. "
        "Write in a clear, precise, and authoritative tone. "
        "Every claim must be supported by the sources provided. "
        "Distinguish clearly between established facts and projections or opinions."
    )
    synthesis_prompt = (
        f"Research question: {query}\n\n"
        f"You have analyzed {len(summarized)} sources. Below are their summaries:\n\n"
        f"{sources_text}\n\n"
        f"Write a comprehensive research synthesis that includes:\n"
        f"## Key Findings\n(3-5 bullet points with [Source N] citations)\n\n"
        f"## Areas of Consensus\n(What sources agree on)\n\n"
        f"## Areas of Uncertainty or Debate\n(Contested claims, conflicting evidence)\n\n"
        f"## Evidence Gaps\n(Important questions the available sources do not answer)\n\n"
        f"## Recommended Further Research\n(2-3 specific directions)\n\n"
        f"Cite sources inline as [Source N] throughout."
    )

    full_synthesis = ""
    # temperature=0.7: 読みやすい文体で総合するため少し高め
    async for token in stream_text(synthesis_prompt, system=system, temperature=0.7):
        full_synthesis += token
        await queue.put(sse_event("synthesis_token", {"text": token}))

    # Save synthesis to session
    session = db.query(ResearchSession).filter(ResearchSession.id == session_id).first()
    if session:
        session.summary = full_synthesis
        session.status = "complete"
        session.topic = _extract_topic(query)
        session.completed_at = datetime.utcnow()
        db.commit()

    await queue.put(sse_event("complete", {
        "session_id": session_id,
        "source_count": len(unique_results),
        "word_count": len(full_synthesis.split()),
        "event_type": "complete",
    }))


def _extract_topic(query: str) -> str:
    """Extract a short topic label from the query (first 60 chars)."""
    return query[:60] + ("..." if len(query) > 60 else "")
