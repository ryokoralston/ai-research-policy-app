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


# "Being Specific" lesson (Anthropic Academy), technique 1: Output Quality
# Guidelines — tell Claude exactly what the output should look like.
_SUMMARY_GUIDELINES = (
    "Write the summary meeting ALL of these guidelines:\n"
    "- Length: 80-150 words total.\n"
    "- Open with a 2-3 sentence overview of the source's main finding.\n"
    "- List 2-3 key claims as bullet points, keeping specific figures, "
    "dates, and named actors.\n"
    "- Note any limitations: bias, opinion-vs-evidence, missing data, or "
    "unsupported assertions.\n"
    "- End with one sentence on relevance to the research query.\n"
    "- Use a neutral, factual tone — do not editorialize."
)

# Technique 2: Process Steps — think-then-write. Kept off by default: the
# eval (eval_being_specific.py) showed steps add no measurable quality gain
# for single-source summarization (a simple task) while making the prompt
# longer. The lesson reserves process steps for complex, multi-angle problems.
_SUMMARY_PROCESS_STEPS = (
    "Before writing, work through these steps internally:\n"
    "1. Identify the source's central thesis or finding.\n"
    "2. Extract the specific, verifiable claims (figures, dates, named actors).\n"
    "3. Assess reliability: is this primary evidence, peer-reviewed research, "
    "opinion, or projection? Note any gaps.\n"
    "4. Judge what this source uniquely contributes to the query.\n\n"
    "Then output ONLY the final summary (not your step-by-step reasoning).\n"
)


def build_source_summary_prompt(
    query: str,
    title: str,
    url: str,
    content: str,
    include_process_steps: bool = False,
) -> str:
    """Build the per-source summarization prompt.

    Applies the "Being Specific" lesson: Output Quality Guidelines always; an
    optional Process Steps preamble (default off — see _SUMMARY_PROCESS_STEPS).

    Extracted as a function (with the include_process_steps seam) so
    evals/eval_being_specific.py can test the exact production prompt and the
    process-steps variant side by side — no duplicated copy that can drift.
    """
    prompt = (
        f"You are summarizing a web source for AI policy research.\n"
        f"Original query: {query}\n"
        f"Source: {title} ({url})\n\n"
        # "Structure with XML tags" lesson: the fetched page is up to 6000 chars
        # of arbitrary text. Wrapping it in a descriptive tag gives Claude a hard
        # boundary so it never mistakes the source body for our instructions.
        f"<source_content>\n{content}\n</source_content>\n\n"
    )
    if include_process_steps:
        prompt += _SUMMARY_PROCESS_STEPS + "\n"
    prompt += _SUMMARY_GUIDELINES
    return prompt


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

    async def _summarize_source(result: TavilyResult) -> str:
        """Summarize a single source. Each call is independent, so all sources
        are summarized concurrently (see asyncio.gather below) instead of one at
        a time — this is the slowest part of the pipeline and dominates latency."""
        content_for_summary = result.content or result.snippet or ""
        # Trim to avoid huge prompts
        if len(content_for_summary) > 6000:
            content_for_summary = content_for_summary[:6000] + "..."

        summary_prompt = build_source_summary_prompt(
            query=query,
            title=result.title,
            url=result.url,
            content=content_for_summary,
        )
        try:
            # temperature=0.3: 事実の要約なので低め（再現性重視）
            return await generate_text(summary_prompt, temperature=0.3)
        except Exception:
            return result.snippet or ""

    # Fan out all per-source summaries in parallel, then process results in the
    # original relevance order so DB result_order and SSE events stay consistent.
    summaries = await asyncio.gather(
        *(_summarize_source(r) for r in unique_results)
    )

    for order, (result, ai_summary) in enumerate(zip(unique_results, summaries)):
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
        f"You have analyzed {len(summarized)} sources. Their summaries are below.\n\n"
        # Wrap the concatenated per-source summaries so Claude treats them as one
        # bounded evidence block, distinct from the synthesis instructions.
        f"<source_summaries>\n{sources_text}\n</source_summaries>\n\n"
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
