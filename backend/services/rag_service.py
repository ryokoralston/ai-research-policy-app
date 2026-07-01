"""RAG pipeline: document indexing and Q&A."""
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

        embed_service = EmbeddingService()
        vs = VectorStore()

        texts = [c.content for c in chunks]
        embeddings = embed_service.embed_texts(texts)

        chunk_ids = []
        db_chunks = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_id = str(uuid.uuid4())
            chunk_ids.append(chunk_id)
            db_chunk = DocumentChunk(
                id=chunk_id,
                document_id=doc_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                page_number=chunk.page_number,
                section_header=chunk.section_header,
                token_count=chunk.token_count,
                chroma_id=chunk_id,
            )
            db_chunks.append(db_chunk)

        # Batch add to ChromaDB
        vs.add_chunks(
            chunk_ids=chunk_ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=[
                {
                    "doc_id": doc_id,
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
                document_id=doc_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                page_number=chunk.page_number,
                section_header=chunk.section_header,
                token_count=chunk.token_count,
                chroma_id=chunk_id,
            ))

        vs.add_chunks(
            chunk_ids=chunk_ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=[
                {
                    "doc_id": doc_id,
                    "page_number": c.page_number,
                    "section_header": c.section_header or "",
                    "chunk_index": c.chunk_index,
                }
                for c in chunks
            ],
        )

        db.bulk_save_objects(db_chunks)
        doc.status = "indexed"
        doc.word_count = word_count
        doc.indexed_at = datetime.utcnow()
        db.commit()
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
}


async def answer_question(
    question: str,
    doc_ids: list[str] | None,
    top_k: int,
    db: Session,
    chat_history: list[dict] | None = None,
    custom_system: str | None = None,
) -> AsyncIterator[str]:
    """Stream an answer via a manual Anthropic tool-use loop.

    Instead of retrieving context upfront, Claude calls the search_documents tool
    when it needs source material. This enables on-demand, query-specific retrieval
    including for follow-up questions.

    chat_history = [{"role": "user"|"assistant", "content": "..."}, ...]
    Previous turns are passed to Claude so it can reference earlier exchanges.
    """
    from rag.retriever import Retriever

    retriever = Retriever()
    collected_chunks: list = []  # accumulates all chunks retrieved across tool calls

    async def execute_tool(name: str, tool_input: dict) -> str:
        """Run a tool call requested by Claude and return the result as a string."""
        # Try reminder tools first; returns None if the name doesn't match any of them
        reminder_result = await execute_reminder_tool(name, tool_input, db)
        if reminder_result is not None:
            return reminder_result

        if name == "search_documents":
            query = tool_input.get("query", "")
            chunks = retriever.retrieve(query, top_k=top_k, doc_ids=doc_ids)
            if not chunks:
                return "No relevant content found in the document library for this query."
            # Collect chunks for citations (deduplicated by chunk_id later)
            collected_chunks.extend(chunks)
            # Fetch all referenced documents in one query (was one query per chunk)
            doc_titles = {
                d.id: (d.title or d.filename)
                for d in db.query(Document).filter(
                    Document.id.in_({c.doc_id for c in chunks})
                )
            }
            # Format exactly like the pre-tool context_parts approach
            context_parts = []
            for chunk in chunks:
                doc_title = doc_titles.get(chunk.doc_id, "Unknown")
                context_parts.append(
                    f"[{doc_title}, p.{chunk.page_number}, sec: {chunk.section_header}]\n{chunk.content}"
                )
            context = "\n\n---\n\n".join(context_parts)
            return f"<source_documents>\n{context}\n</source_documents>"
        return f"Unknown tool: {name}"

    # Build system prompt: describe the tool and citation requirements
    default_system = (
        "You are a research assistant for an AI policy institute. "
        "Answer questions based only on material returned by the search_documents tool. "
        "Before answering any substantive question, call search_documents with a relevant query. "
        "Be concise and direct — aim for 3–5 sentences unless the question requires more detail. "
        "Cite sources using [Doc Title] format. "
        "If the tool returns no relevant content, say so explicitly. "
        "You have access to the conversation history — use it to answer follow-up questions naturally. "
        "You can also set reminders for the user. "
        "For any relative date or time expression ('next Thursday', 'in two weeks', 'a week from Friday'), "
        "you MUST call get_current_datetime first, then add_duration_to_datetime to compute the exact "
        "target datetime, and finally call set_reminder — never compute dates yourself."
    )
    system = (
        f"{custom_system}\n\n"
        "Additional constraints: Answer based only on material returned by the search_documents tool. "
        "Call the tool before answering substantive questions. "
        "Cite sources using [Doc Title] format. "
        "If the tool returns no relevant content, say so explicitly. "
        "You can also set reminders for the user. "
        "For any relative date or time ('next Thursday', 'in two weeks', 'a week from Friday'), "
        "call get_current_datetime first, then add_duration_to_datetime, then set_reminder — "
        "never compute dates yourself."
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
    # temperature=0.3: ドキュメントに基づく事実回答なので低め
    async for event_type, payload in stream_chat_with_tools(
        messages,
        system=system,
        tools=[SEARCH_DOCUMENTS_TOOL, *REMINDER_TOOLS],
        tool_executor=execute_tool,
        temperature=0.3,
    ):
        if event_type == "tool_use":
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

    # Deduplicate collected_chunks by chunk_id, preserving encounter order
    seen_ids: set[str] = set()
    deduped_chunks = []
    for chunk in collected_chunks:
        if chunk.chunk_id not in seen_ids:
            seen_ids.add(chunk.chunk_id)
            deduped_chunks.append(chunk)

    citations = [
        {"doc_id": c.doc_id, "chunk_id": c.chunk_id, "page": c.page_number}
        for c in deduped_chunks
    ]
    yield sse_event("complete", {"citations": citations, "word_count": len(full_text.split())})
