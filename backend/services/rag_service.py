"""RAG pipeline: document indexing and Q&A."""
import re
import uuid
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy.orm import Session

from models import Document, DocumentChunk
from rag.chunker import chunk_pdf, chunk_html, chunk_plain_text, TextChunk, _approx_tokens
from rag.vector_store import VectorStore
from services.embedding_service import EmbeddingService
from services.anthropic_client import stream_text, stream_chat, stream_chat_with_tools, sse_event, UNTRUSTED_CONTENT_GUARD
from services.reminder_tools import REMINDER_TOOLS, execute_reminder_tool
from services.text_editor_tool import TEXT_EDITOR_TOOL, TEXT_EDITOR_TOOL_NAME, execute_text_editor_tool


def _embed_and_store(
    doc: Document,
    chunks: list[TextChunk],
    db: Session,
    *,
    page_count: int | None = None,
    word_count: int | None = None,
) -> None:
    """Embed chunks, write them to ChromaDB + SQLite, and mark the document indexed.

    Shared by index_document and index_web_content (F-1): both did
    "embed -> chunk_id assignment -> DocumentChunk construction -> vs.add_chunks
    (identical metadata dict) -> bulk_save_objects -> doc.status update" as
    near-duplicate ~50-line blocks. The metadata schema written here
    (doc_id/page_number/section_header/chunk_index) is a contract with
    rag/vector_store.py's query side — keeping it in one place means the two
    can no longer drift apart.

    Deliberately does NOT catch exceptions: the two callers have different
    failure policies (index_document marks status=error and re-raises;
    index_web_content marks status=error and swallows, since it runs as a
    background task) so that policy stays in their own try/except blocks.
    """
    embed_service = EmbeddingService()
    vs = VectorStore()

    texts = [c.content for c in chunks]
    embeddings = embed_service.embed_texts(texts)

    chunk_ids = []
    db_chunks = []
    for chunk, embedding in zip(chunks, embeddings):
        chunk_id = str(uuid.uuid4())
        chunk_ids.append(chunk_id)
        db_chunks.append(DocumentChunk(
            id=chunk_id,
            document_id=doc.id,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            page_number=chunk.page_number,
            section_header=chunk.section_header,
            token_count=chunk.token_count,
            chroma_id=chunk_id,
        ))

    # Batch add to ChromaDB
    vs.add_chunks(
        chunk_ids=chunk_ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=[
            {
                "doc_id": doc.id,
                "page_number": c.page_number,
                "section_header": c.section_header or "",
                "chunk_index": c.chunk_index,
            }
            for c in chunks
        ],
    )

    db.bulk_save_objects(db_chunks)
    doc.status = "indexed"
    doc.page_count = page_count
    doc.word_count = word_count
    doc.indexed_at = datetime.utcnow()
    db.commit()


async def index_document(doc_id: str, db: Session) -> None:
    """Chunk file and index into ChromaDB. Supports PDF, TXT, HTML. Called as a background task."""
    import os as _os
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or not doc.file_path:
        return

    try:
        ext = _os.path.splitext(doc.file_path)[1].lower()
        if ext == ".pdf":
            chunks, page_count, word_count = chunk_pdf(doc.file_path)
        elif ext in (".html", ".htm"):
            with open(doc.file_path, encoding="utf-8", errors="ignore") as f:
                chunks, word_count = chunk_html(f.read())
            page_count = None
        else:  # .txt or transcript
            with open(doc.file_path, encoding="utf-8", errors="ignore") as f:
                chunks, word_count = chunk_plain_text(f.read())
            page_count = None

        if not chunks:
            doc.status = "error"
            doc.page_count = page_count
            doc.word_count = word_count
            db.commit()
            return

        _embed_and_store(doc, chunks, db, page_count=page_count, word_count=word_count)

    except Exception as e:
        doc.status = "error"
        db.commit()
        raise


async def index_web_content(doc_id: str, content: str, db: Session) -> None:
    """Chunk and index web content (save-to-library) into ChromaDB.

    Uses the standard chunker (chunk_plain_text) so web sources get the same
    granularity, section awareness, and overlap as uploaded documents.

    Short-content fallback: the chunker drops trailing chunks under its
    minimum token threshold, but save-to-library sources are sometimes just
    an AI summary or snippet — those must still be searchable, so content the
    chunker rejects entirely is indexed as one single chunk.

    Empty content is not an error here (unlike index_document): a source with
    no usable text is simply marked indexed with zero chunks, matching the
    behavior of the original router implementation this replaces.
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        return

    try:
        chunks, word_count = chunk_plain_text(content)

        if not chunks and content.strip():
            text = content.strip()
            chunks = [TextChunk(
                content=text,
                page_number=1,
                section_header="",
                chunk_index=0,
                token_count=_approx_tokens(text),
            )]

        if not chunks:
            doc.status = "indexed"
            db.commit()
            return

        _embed_and_store(doc, chunks, db, word_count=word_count)
    except Exception:
        # Background task — record the failure on the document instead of raising
        doc.status = "error"
        db.commit()


# Tool definition for the document-library Q&A agentic loop.
# Claude calls this tool to retrieve relevant passages on demand instead of
# receiving pre-stuffed context.
SEARCH_DOCUMENTS_TOOL = {
    "name": "search_documents",
    "description": (
        "Search the user's uploaded document library for passages relevant to a query. "
        "Call this whenever you need source material to answer the question — including "
        "follow-up questions, where you should write a self-contained query that captures "
        "what the user is really asking about."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A self-contained search query in English describing the information needed",
            }
        },
        "required": ["query"],
    },
    # Fine-grained tool streaming (GA — no beta header needed). Disables
    # server-side buffering/validation of the streamed input JSON, so the
    # query streams out token-by-token instead of arriving all at once when
    # the tool_use block closes. Trade-off: the display path (see
    # _partial_query_from_snapshot below) must tolerate partial/invalid JSON
    # while a delta is mid-token. Not applied to the reminder tools — their
    # arguments are short enough that buffered delivery is already instant.
    "eager_input_streaming": True,
}


def _partial_query_from_snapshot(snapshot) -> str | None:
    """Best-effort extraction of the growing "query" string value from a
    streamed search_documents input snapshot, for live progress display.

    snapshot may be:
      - a dict (SDK's tolerant partial-JSON parser has already produced a
        parsed object) — read "query" directly if it's a string.
      - a str (fallback / defensive case) — the JSON is by definition
        incomplete mid-stream (e.g. '{"query": "EU AI Ac'), so it is never
        run through json.loads; instead a tolerant regex pulls out the
        growing string value even though the surrounding JSON is invalid.
      - anything else — returns None.

    Returns None (never raises) when no usable partial value can be found,
    so callers can simply skip the SSE yield in that case.
    """
    if isinstance(snapshot, dict):
        query = snapshot.get("query")
        return query if isinstance(query, str) and query else None
    if isinstance(snapshot, str):
        match = re.search(r'"query"\s*:\s*"((?:[^"\\]|\\.)*)', snapshot)
        if not match:
            return None
        raw = match.group(1)
        # Minimal unescape of the two characters most likely to appear
        # mid-stream — this is a display-only best-effort value, not a
        # full JSON string decoder.
        unescaped = raw.replace('\\"', '"').replace("\\\\", "\\")
        return unescaped or None
    return None


async def answer_question(
    question: str,
    doc_ids: list[str] | None,
    top_k: int,
    db: Session,
    chat_history: list[dict] | None = None,
    custom_system: str | None = None,
    prior_citations: list[dict] | None = None,
) -> AsyncIterator[str]:
    """Stream an answer via a manual Anthropic tool-use loop.

    Instead of retrieving context upfront, Claude calls the search_documents tool
    when it needs source material. This enables on-demand, query-specific retrieval
    including for follow-up questions.

    chat_history = [{"role": "user"|"assistant", "content": "..."}, ...]
    Previous turns are passed to Claude so it can reference earlier exchanges.
    Assistant turn content may be plain text or a list of block dicts (replayed
    tool_use/tool_result history — see anthropic_client.serialize_content_blocks).

    prior_citations: cumulative citations from previous turns (the "citations"
    list of the last "complete" event), used to keep [N] numbering stable across
    turns instead of restarting at [1] every turn.
    """
    from rag.retriever import Retriever

    retriever = Retriever()
    # Sentence-level numbered citations: chunk_id -> 1-based index, assigned in
    # first-seen order across (possibly multiple) tool calls in this turn.
    # ordered_citations is built incrementally and is therefore already
    # deduplicated by construction — no separate dedup pass needed at the end.
    citation_index: dict[str, int] = {}
    ordered_citations: list[dict] = []
    for c in prior_citations or []:
        citation_index[c["chunk_id"]] = c["index"]
        ordered_citations.append(c)
    # New chunks continue numbering after the highest existing index (indices are
    # contiguous by construction, but max() is safer than trusting that invariant).
    next_citation_index = max((c["index"] for c in ordered_citations), default=0)

    async def execute_tool(name: str, tool_input: dict) -> str:
        """Run a tool call requested by Claude and return the result as a string.

        Raises ValueError for an unrecognized tool name so the caller's _run_tool
        (anthropic_client.stream_chat_with_tools) converts it into an is_error=True
        tool_result — an unknown-tool string result would otherwise look like a
        normal success to Claude, leaving it unable to tell the call failed.
        """
        nonlocal next_citation_index
        # Try reminder tools first; returns None if the name doesn't match any of them
        reminder_result = await execute_reminder_tool(name, tool_input, db)
        if reminder_result is not None:
            return reminder_result

        if name == "search_documents":
            query = tool_input.get("query", "")
            chunks = retriever.retrieve(query, top_k=top_k, doc_ids=doc_ids)
            if not chunks:
                return "No relevant content found in the document library for this query."
            # Fetch all referenced documents in one query (was one query per chunk)
            doc_titles = {
                d.id: (d.title or d.filename)
                for d in db.query(Document).filter(
                    Document.id.in_({c.doc_id for c in chunks})
                )
            }
            # Format exactly like the pre-tool context_parts approach, prefixed
            # with the citation number Claude should cite inline as [N].
            context_parts = []
            for chunk in chunks:
                doc_title = doc_titles.get(chunk.doc_id, "Unknown")
                if chunk.chunk_id not in citation_index:
                    next_citation_index += 1
                    citation_index[chunk.chunk_id] = next_citation_index
                    ordered_citations.append({
                        "index": citation_index[chunk.chunk_id],
                        "doc_id": chunk.doc_id,
                        "chunk_id": chunk.chunk_id,
                        "page": chunk.page_number,
                        "title": doc_title,
                        "snippet": chunk.content[:200],
                    })
                context_parts.append(
                    f"[{citation_index[chunk.chunk_id]}] [{doc_title}, p.{chunk.page_number}, "
                    f"sec: {chunk.section_header}]\n{chunk.content}"
                )
            context = "\n\n---\n\n".join(context_parts)
            return f"<source_documents>\n{context}\n</source_documents>"

        if name == TEXT_EDITOR_TOOL_NAME:
            return await execute_text_editor_tool(tool_input)
        raise ValueError(f"Unknown tool: {name}")

    # Build system prompt: describe the tool and citation requirements
    default_system = (
        "You are a research assistant for an AI policy institute. "
        "Answer questions based only on material returned by the search_documents tool. "
        "Before answering any substantive question, call search_documents with a relevant query. "
        "Be concise and direct — aim for 3–5 sentences unless the question requires more detail. "
        "Cite sources using the bracketed number shown before each source in the search results "
        "(e.g. [1]). Place the citation number immediately after the specific sentence or claim it "
        "supports — do not just cite once at the end. If a claim draws on multiple sources, cite "
        "each one, e.g. [1][3]. "
        "If the tool returns no relevant content, say so explicitly. "
        "You have access to the conversation history — use it to answer follow-up questions naturally. "
        "Search results from earlier turns remain visible in the conversation history — if they "
        "already contain what you need, you may cite their bracketed numbers directly without "
        "searching again. "
        "You can also set reminders for the user. "
        "For any relative date or time expression ('next Thursday', 'in two weeks', 'a week from Friday'), "
        "you MUST call get_current_datetime first, then add_duration_to_datetime to compute the exact "
        "target datetime, and finally call set_reminder — never compute dates yourself. "
        "You also have a draft workspace: use the text editor tool to create and revise draft files "
        "(memos, briefs, notes) when the user asks you to draft, save, or edit a document — refer to "
        "files by simple relative names like 'briefing.md'."
    )
    system = (
        f"{custom_system}\n\n"
        "Additional constraints: Answer based only on material returned by the search_documents tool. "
        "Call the tool before answering substantive questions. "
        "Cite sources using the bracketed number shown before each source in the search results "
        "(e.g. [1]). Place the citation number immediately after the specific sentence or claim it "
        "supports — do not just cite once at the end. If a claim draws on multiple sources, cite "
        "each one, e.g. [1][3]. "
        "If the tool returns no relevant content, say so explicitly. "
        "Search results from earlier turns remain visible in the conversation history — if they "
        "already contain what you need, you may cite their bracketed numbers directly without "
        "searching again. "
        "You can also set reminders for the user. "
        "For any relative date or time ('next Thursday', 'in two weeks', 'a week from Friday'), "
        "call get_current_datetime first, then add_duration_to_datetime, then set_reminder — "
        "never compute dates yourself. "
        "You also have a draft workspace: use the text editor tool to create and revise draft files "
        "(memos, briefs, notes) when the user asks you to draft, save, or edit a document — refer to "
        "files by simple relative names like 'briefing.md'."
        if custom_system else default_system
    )
    # The retrieved chunks are untrusted document content — guard against any
    # injected instructions hiding inside them.
    system = f"{system}\n\n{UNTRUSTED_CONTENT_GUARD}"

    # Build messages: chat history + the bare question (no pre-stuffed context)
    messages = list(chat_history or [])
    messages.append({"role": "user", "content": question})

    yield sse_event("start", {"question": question})

    full_text = ""
    turn_messages: list[dict] = []
    # Raw accumulated input JSON of the currently-streaming search_documents
    # call. The SDK's parsed dict snapshot drops string values until their
    # closing quote arrives (jiter partial_mode), so the dict path only
    # produces the query once it's complete — accumulating the raw
    # partial_json chunks ourselves is what makes the query grow
    # token-by-token in the UI.
    search_input_raw = ""
    # temperature=0.3: ドキュメントに基づく事実回答なので低め
    async for event_type, payload in stream_chat_with_tools(
        messages,
        system=system,
        tools=[SEARCH_DOCUMENTS_TOOL, *REMINDER_TOOLS, TEXT_EDITOR_TOOL],
        tool_executor=execute_tool,
        temperature=0.3,
    ):
        if event_type == "tool_pending":
            # Fired the instant Claude commits to a tool call, before its
            # arguments exist — lets the UI show an indicator immediately
            # instead of waiting for the whole tool_use block to finish.
            if payload["name"] == "search_documents":
                search_input_raw = ""  # new block — reset the accumulator
            yield sse_event("tool_pending", {"name": payload["name"]})
        elif event_type == "tool_input_delta":
            # Only search_documents has eager_input_streaming enabled, so this
            # is the only tool whose arguments arrive incrementally; other
            # tools' input_json events collapse to a single delta at block
            # close, which the "tool" event below already covers.
            if payload["name"] == "search_documents":
                search_input_raw += payload["partial_json"]
                # Raw accumulation first (grows per token); dict snapshot as
                # fallback (complete values only).
                partial_query = (
                    _partial_query_from_snapshot(search_input_raw)
                    or _partial_query_from_snapshot(payload["snapshot"])
                )
                if partial_query:
                    yield sse_event("tool_progress", {
                        "name": payload["name"],
                        "query": partial_query,
                    })
        elif event_type == "tool_use":
            # Keep "query" field for search_documents so existing frontend code doesn't break;
            # add "input" with the full tool input for all tools (new frontend uses this).
            yield sse_event("tool", {
                "name": payload["name"],
                "query": payload["input"].get("query", ""),
                "input": payload["input"],
            })
        elif event_type == "text":
            full_text += payload
            yield sse_event("token", {"text": payload})
        elif event_type == "turn_messages":
            turn_messages = payload

    # ordered_citations is already deduplicated by chunk_id and in first-seen
    # (index) order — built incrementally inside execute_tool above.
    # turn_messages: block-level messages this turn produced (see
    # anthropic_client.serialize_content_blocks) — the frontend replays them as
    # chat_history on the next turn so prior tool_use/tool_result blocks survive.
    yield sse_event("complete", {
        "citations": ordered_citations,
        "turn_messages": turn_messages,
        "word_count": len(full_text.split()),
    })
