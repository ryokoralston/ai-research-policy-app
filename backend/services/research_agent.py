"""
Research Agent: orchestrates web search + Claude synthesis.

Pipeline:
  1. Query decomposition (Claude generates sub-queries)
  2. Parallel Tavily searches
  3. Per-source summarization (fast model — see ModelSettings/config)
  4. Final synthesis (main model, streaming)
  5. Evidence-gap-closing loop (bounded, up to MAX_GAP_ITERATIONS rounds):
     Claude reviews its own "## Evidence Gaps" section, and if it names
     specific gaps that web search could plausibly close, this searches for
     more sources and re-synthesizes with the cumulative evidence. See
     build_gap_check_prompt / MAX_GAP_ITERATIONS below.
"""
import asyncio
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from models import ResearchSession, SearchResult
from services.anthropic_client import (
    generate_json,
    generate_text,
    stream_text,
    sse_event,
    UNTRUSTED_CONTENT_GUARD,
)
from services.tavily_client import TavilyClient, SearchResult as TavilyResult


# Evidence-gap-closing loop (see build_gap_check_prompt / run_research_agent
# step 5-ish below): bounded like report_quality.py's revise_if_ungrounded,
# but for a different concern — instead of revising existing claims against a
# grader, this closes gaps in coverage by searching for more sources. Capped
# at a small, fixed number of extra rounds so a synthesis that keeps finding
# "one more gap" can't turn into an unbounded, unboundedly expensive loop.
MAX_GAP_ITERATIONS = 2

# Per-iteration caps on the gap-closing round (kept far below the main search
# step): this is a follow-up round to fill specific holes, not a full re-search.
GAP_SEARCH_MAX_RESULTS_PER_QUERY = 3
GAP_MAX_NEW_SOURCES_PER_ITERATION = 6


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


def build_decomposition_prompt(query: str) -> str:
    """Build the query-decomposition prompt.

    "Providing Examples" lesson: a worked multi-shot example (XML-wrapped, with
    a note on *why* the output is good) shows Claude the target — three queries
    that hit distinct angles, stay specific, and don't echo the question. The
    example question is intentionally NOT one of the eval dataset cases, so the
    eval does not get to "see the answer" for any case it scores.

    Extracted as a function so evals/eval_research_queries.py tests the exact
    production prompt (no copy that can drift). Note: the production call adds
    prefill='```json' + stop_sequences, so this text must NOT include a fence.
    """
    return (
        "You are a policy research assistant. Given a research question, "
        "generate exactly 3 specific search queries that together provide "
        "comprehensive coverage from distinct angles.\n\n"
        "Here is an example research question with an ideal decomposition:\n"
        "<example>\n"
        "<sample_input>What are the privacy implications of AI-powered "
        "surveillance in public spaces?</sample_input>\n"
        "<ideal_output>[\"legal frameworks governing AI surveillance and "
        "facial recognition in public spaces\", \"civil liberties and privacy "
        "rights concerns raised by AI public surveillance systems\", "
        "\"comparative government policies on AI surveillance deployment and "
        "oversight\"]</ideal_output>\n"
        "This output is ideal because the three queries cover distinct angles "
        "(legal frameworks, civil liberties, comparative policy), each adds "
        "specific terms not present in the original question, and none simply "
        "restates it.\n"
        "</example>\n\n"
        f"Research question: {query}\n\n"
        'Return ONLY a JSON array of 3 strings, like: ["query1", "query2", "query3"]'
    )


def build_gap_check_prompt(query: str, synthesis: str) -> str:
    """Build the evidence-gap-check prompt: given the research question and the
    current synthesis (which already contains an "## Evidence Gaps" section),
    ask Claude whether any of those gaps are SPECIFIC enough that additional
    targeted web search could plausibly close them.

    "Providing Examples" lesson, same technique as build_decomposition_prompt:
    a worked XML example shows the target — specific, searchable gap queries,
    and the discipline to return an empty array when a gap isn't the kind of
    thing web search can resolve (e.g. it requires primary/proprietary data)
    or the synthesis is already well covered. The example question is not one
    of the eval dataset cases.

    Extracted as a function (mirrors build_decomposition_prompt /
    build_source_summary_prompt) so it can be unit tested without a live API
    call. Note: the production call adds prefill='```json' + stop_sequences,
    so this text must NOT include a fence.
    """
    return (
        "You are a policy research assistant reviewing a research synthesis for "
        "remaining evidence gaps. The synthesis below includes an \"## Evidence "
        "Gaps\" section listing questions the available sources did not answer.\n\n"
        "Your job: identify 1-3 gaps that are SPECIFIC enough that an "
        "additional, targeted web search could plausibly close them. Skip gaps "
        "that are already reasonably well covered elsewhere in the synthesis, "
        "and skip gaps that no web search can resolve — e.g. they require "
        "primary or proprietary data (unpublished government data, internal "
        "company figures), original polling, or expert interviews rather than "
        "published sources. If no gap qualifies, return an empty array.\n\n"
        "Here is an example synthesis excerpt with an ideal gap assessment:\n"
        "<example>\n"
        "<sample_input>## Evidence Gaps\n"
        "- The synthesis does not address how enforcement penalties for the EU "
        "AI Act's high-risk category compare across member states.\n"
        "- No source quantifies the internal compliance costs individual "
        "companies expect to bear.\n"
        "- It is unclear how proactively national regulators will audit "
        "deployed systems versus responding to complaints.</sample_input>\n"
        "<ideal_output>[\"EU AI Act high-risk category enforcement penalties by "
        "member state comparison\", \"national AI regulator proactive audit "
        "versus complaint-driven enforcement approach\"]</ideal_output>\n"
        "This output is ideal because it turns two of the three gaps into "
        "specific, searchable queries, and correctly DROPS the internal "
        "compliance-cost gap — that requires proprietary company data no "
        "public web search would surface.\n"
        "</example>\n\n"
        f"Research question: {query}\n\n"
        f"<synthesis>\n{synthesis}\n</synthesis>\n\n"
        "Return ONLY a JSON array of 0-3 search query strings, like: "
        '["query1", "query2"] or [] if no gap qualifies.'
    )


def build_synthesis_prompt(query: str, summarized: list[dict]) -> str:
    """Build the final synthesis prompt from the per-source summaries.

    Extracted out of run_research_agent (previously built inline) so the
    evidence-gap-closing loop can call it again each iteration with the
    cumulative `summarized` list (original sources + every gap round's new
    sources), producing a fresh synthesis that supersedes the previous one.
    Mirrors build_decomposition_prompt / build_source_summary_prompt: a named,
    pure prompt builder with no duplicated prompt text between call sites.
    """
    sources_text = "\n\n".join(
        f"[Source {s['order']}] {s['title']} ({s['url']})\n{s['summary']}"
        for s in summarized
    )
    return (
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


async def run_research_agent(
    session_id: str,
    query: str,
    max_sources: int,
    queue: asyncio.Queue,
    db: Session,
) -> None:
    # ── Step 1: Query Decomposition ───────────────────────────────────────────
    await queue.put(sse_event("status", {"message": "Decomposing research query..."}))

    decomp_prompt = build_decomposition_prompt(query)
    try:
        # generate_json: prefill forces a JSON array, the stop sequence cuts
        # off any trailing explanation text (see anthropic_client.generate_json)
        sub_queries: list[str] = await generate_json(decomp_prompt, temperature=0.2)
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
            # System guard: the page body is untrusted — treat it as data only.
            return await generate_text(
                summary_prompt, system=UNTRUSTED_CONTENT_GUARD, temperature=0.3
            )
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

    system = (
        "You are a senior AI policy analyst at a leading think tank. "
        "Your syntheses are used to brief members of Congress and senior policy officials. "
        "Write in a clear, precise, and authoritative tone. "
        "Every claim must be supported by the sources provided. "
        "Distinguish clearly between established facts and projections or opinions."
        "\n\n" + UNTRUSTED_CONTENT_GUARD
    )

    full_synthesis = ""
    synthesis_prompt = build_synthesis_prompt(query, summarized)
    # temperature=0.7: 読みやすい文体で総合するため少し高め
    async for token in stream_text(synthesis_prompt, system=system, temperature=0.7):
        full_synthesis += token
        await queue.put(sse_event("synthesis_token", {"text": token}))

    # ── Step 5: Evidence-gap-closing loop (bounded, see MAX_GAP_ITERATIONS) ───
    # Evaluator-optimizer pattern, same family as report_quality.py's
    # revise_if_ungrounded but for a different concern: instead of revising
    # existing claims against a grader, this searches for more sources to
    # close gaps the synthesis itself flagged in its "## Evidence Gaps"
    # section. Bounded to MAX_GAP_ITERATIONS rounds, each capped to a handful
    # of cheap, targeted searches, so a synthesis that always finds "one more
    # gap" can't turn this into an unbounded, unboundedly expensive loop.
    gap_reason = "no_gaps"
    gap_iterations_run = 0

    for iteration in range(1, MAX_GAP_ITERATIONS + 1):
        gap_check_prompt = build_gap_check_prompt(query, full_synthesis)
        try:
            gap_queries = await generate_json(gap_check_prompt, temperature=0.2)
            if not isinstance(gap_queries, list):
                gap_queries = []
            gap_queries = [q for q in gap_queries if isinstance(q, str) and q.strip()][:3]
        except Exception:
            # Never let the gap-closing loop crash the pipeline — any failure
            # here (bad JSON, API error) just stops the loop as if no gaps
            # were found.
            gap_queries = []

        if not gap_queries:
            gap_reason = "no_gaps"
            break

        await queue.put(sse_event("gap_queries", {"queries": gap_queries}))
        await queue.put(sse_event("status", {
            "message": f"Researching {len(gap_queries)} evidence gap(s)..."
        }))

        gap_search_tasks = [
            tavily.search(q, max_results=GAP_SEARCH_MAX_RESULTS_PER_QUERY, search_depth="advanced")
            for q in gap_queries
        ]
        gap_results_nested = await asyncio.gather(*gap_search_tasks, return_exceptions=True)

        # Dedupe against the SAME seen_urls set from Step 2, so gap-round
        # sources never duplicate anything already found (original or prior
        # gap rounds).
        new_unique: list[TavilyResult] = []
        for batch in gap_results_nested:
            if isinstance(batch, Exception):
                continue
            for r in batch:
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    new_unique.append(r)

        new_unique.sort(key=lambda r: r.score, reverse=True)
        new_unique = new_unique[:GAP_MAX_NEW_SOURCES_PER_ITERATION]

        if not new_unique:
            gap_reason = "no_new_sources"
            break

        await queue.put(sse_event("sources_found", {"count": len(new_unique)}))
        await queue.put(sse_event("status", {"message": "Summarizing new sources..."}))

        new_summaries = await asyncio.gather(
            *(_summarize_source(r) for r in new_unique)
        )

        for result, ai_summary in zip(new_unique, new_summaries):
            order = len(summarized)
            db_result = SearchResult(
                id=str(uuid.uuid4()),
                session_id=session_id,
                url=result.url,
                title=result.title,
                snippet=result.snippet,
                full_content=(result.content or "")[:10000],
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

        # A brand-new synthesis is about to stream, superseding the previous
        # one — resynthesis_start lets the frontend distinguish this from
        # "more tokens of the same synthesis" (see synthesis_token above).
        await queue.put(sse_event("resynthesis_start", {"iteration": iteration}))
        await queue.put(sse_event("status", {"message": "Re-synthesizing findings with new evidence..."}))

        resynthesis_prompt = build_synthesis_prompt(query, summarized)
        full_synthesis = ""
        async for token in stream_text(resynthesis_prompt, system=system, temperature=0.7):
            full_synthesis += token
            await queue.put(sse_event("synthesis_token", {"text": token}))

        gap_iterations_run = iteration
        # Tentative reason if this was the last allowed iteration; overwritten
        # by "no_gaps" / "no_new_sources" if a subsequent iteration breaks
        # early instead.
        gap_reason = "max_iterations"

    await queue.put(sse_event("gaps_closed", {
        "reason": gap_reason,
        "iterations": gap_iterations_run,
    }))

    # Save synthesis to session (final synthesis — whichever iteration ran last)
    session = db.query(ResearchSession).filter(ResearchSession.id == session_id).first()
    if session:
        session.summary = full_synthesis
        session.status = "complete"
        session.topic = _extract_topic(query)
        session.completed_at = datetime.utcnow()
        db.commit()

    await queue.put(sse_event("complete", {
        "session_id": session_id,
        "source_count": len(summarized),
        "word_count": len(full_synthesis.split()),
        "event_type": "complete",
        "gap_iterations": gap_iterations_run,
    }))


def _extract_topic(query: str) -> str:
    """Extract a short topic label from the query (first 60 chars)."""
    return query[:60] + ("..." if len(query) > 60 else "")
