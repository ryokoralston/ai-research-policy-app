"""Contextual Retrieval (Anthropic technique): generate a short, situating
context for each chunk at index time, from the whole document, and prepend
it to the chunk before embedding/BM25 indexing. This gives both the
embedding model and the lexical index signal that a chunk in isolation often
lacks (e.g. "this is Q3 revenue" without knowing which company/quarter),
without polluting the citation snippet shown to users (see rag_service.py:
documents stay the ORIGINAL chunk text; only the match/embed text carries
the prepended context).

See: https://www.anthropic.com/news/contextual-retrieval

Config-gated by settings.contextual_retrieval_enabled (config.py) — when
False, contextualize_chunks returns empty contexts for every chunk and the
indexing funnel's writes are byte-identical to pre-feature behavior (see
rag_service._embed_and_store).
"""
import asyncio
import sys

from config import get_settings
from services.anthropic_client import generate_text

# Full-document prefix cap for context generation. A 100k-char cap keeps the
# per-chunk prompt (document + chunk) within a sane cached-prefix size even
# for very large documents — the situating context only needs enough of the
# document to place a chunk, not the literal entire text for book-length
# sources.
MAX_DOC_CHARS = 100_000

_TRUNCATION_MARKER = "\n[... document truncated for context generation]"


def build_document_prefix(full_text: str) -> str:
    """Wrap the (possibly truncated) full document in <document> tags with a
    one-line instruction that this is the whole document, for situating
    chunks drawn from it. Pure function — no I/O, no API call.
    """
    text = full_text
    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS] + _TRUNCATION_MARKER
    return (
        "<document>\n"
        f"{text}\n"
        "</document>\n\n"
        "The above is the whole document (or as much of it as fits) that the "
        "chunks below are drawn from."
    )


def build_context_prompt(chunk_content: str) -> str:
    """Build the per-chunk prompt asking Claude to situate `chunk_content`
    within the document (sent as the cached_prefix by contextualize_chunks).
    Pure function — mirrors Anthropic's published contextual-retrieval
    prompt.
    """
    return (
        "Here is the chunk we want to situate within the whole document\n"
        "<chunk>\n"
        f"{chunk_content}\n"
        "</chunk>\n\n"
        "Give a short succinct context (1-2 sentences) situating this chunk "
        "within the overall document for the purposes of improving search "
        "retrieval of the chunk. Answer only with the succinct context and "
        "nothing else."
    )


def combine(context: str, content: str) -> str:
    """Prepend `context` to `content` for embedding/BM25 matching. Returns
    `content` unchanged when context is empty (flag off, or this chunk's
    context generation failed) — pure function.
    """
    if not context:
        return content
    return f"{context}\n\n{content}"


async def contextualize_chunks(
    full_text: str,
    chunk_texts: list[str],
    concurrency: int = 4,
) -> list[str]:
    """Generate a situating context for each chunk in chunk_texts, aligned
    by position. Returns [""] * len(chunk_texts) when the feature is
    disabled (settings.contextual_retrieval_enabled is False) or
    chunk_texts is empty — callers can always call combine(ctx, content)
    unconditionally on the result.

    Cache-warm order: the document prefix (build_document_prefix(full_text))
    is sent as generate_text's cached_prefix on every call, so Anthropic's
    prompt cache can serve every chunk after the first from the cached
    prefix instead of re-reading (and re-billing) the whole document each
    time. To get exactly ONE cache write for that prefix, the FIRST chunk's
    call is awaited alone (which creates the cache entry), and only THEN are
    the remaining chunks fanned out concurrently (bounded by
    asyncio.Semaphore(concurrency)) — issuing all calls in parallel from the
    start would have every one of them race to write the same cache entry,
    each paying the (more expensive) cache-write price instead of N-1 of
    them reading a warm cache.

    Per-chunk exception -> context "" for that chunk (one stderr line per
    failure) — a failed context must never fail indexing, since content
    still indexes fine as content alone.
    """
    if not chunk_texts:
        return []
    if not get_settings().contextual_retrieval_enabled:
        return [""] * len(chunk_texts)

    prefix = build_document_prefix(full_text)
    results: list[str] = [""] * len(chunk_texts)

    async def _one(index: int) -> None:
        try:
            raw = await generate_text(
                build_context_prompt(chunk_texts[index]),
                cached_prefix=prefix,
                temperature=0.0,
                max_tokens=150,
            )
            results[index] = raw.strip()
        except Exception as exc:
            print(f"contextualize_chunks: failed for chunk index {index}: {exc}", file=sys.stderr)
            results[index] = ""

    # Cache-warm the prefix with a single solo call first (see docstring),
    # then fan out the rest bounded by the semaphore.
    await _one(0)

    if len(chunk_texts) > 1:
        semaphore = asyncio.Semaphore(concurrency)

        async def _bounded(index: int) -> None:
            async with semaphore:
                await _one(index)

        await asyncio.gather(*(_bounded(i) for i in range(1, len(chunk_texts))))

    return results
