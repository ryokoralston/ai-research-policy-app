"""RAG pipeline: document indexing and Q&A."""
import uuid
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy.orm import Session

from models import Document, DocumentChunk
from rag.chunker import chunk_pdf, chunk_html, chunk_plain_text
from rag.vector_store import VectorStore
from services.embedding_service import EmbeddingService
from services.anthropic_client import stream_text, stream_chat, sse_event


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


async def answer_question(
    question: str,
    doc_ids: list[str] | None,
    top_k: int,
    db: Session,
    chat_history: list[dict] | None = None,
    custom_system: str | None = None,
) -> AsyncIterator[str]:
    """Stream an answer using retrieved document chunks.
    chat_history = [{"role": "user"|"assistant", "content": "..."}, ...]
    Previous turns are passed to Claude so it can reference earlier exchanges.
    """
    from rag.retriever import Retriever

    retriever = Retriever()
    chunks = retriever.retrieve(question, top_k=top_k, doc_ids=doc_ids)

    if not chunks:
        yield sse_event("error", {"message": "No relevant content found in the selected documents."})
        return

    # Build context with document title info
    context_parts = []
    for chunk in chunks:
        doc = db.query(Document).filter(Document.id == chunk.doc_id).first()
        doc_title = doc.title or doc.filename if doc else "Unknown"
        context_parts.append(
            f"[{doc_title}, p.{chunk.page_number}, sec: {chunk.section_header}]\n{chunk.content}"
        )
    context = "\n\n---\n\n".join(context_parts)

    # Use custom system prompt if provided, otherwise fall back to default
    default_system = (
        "You are a research assistant for an AI policy institute. "
        "Answer questions based only on the provided source documents. "
        "Be concise and direct — aim for 3–5 sentences unless the question requires more detail. "
        "Cite sources using [Doc Title] format. "
        "If the documents do not contain enough information to answer, say so explicitly. "
        "You have access to the conversation history — use it to answer follow-up questions naturally."
    )
    system = (
        f"{custom_system}\n\nAdditional constraint: Answer based only on the provided source documents. "
        f"Cite sources using [Doc Title] format. "
        f"If the documents do not contain enough information to answer, say so explicitly."
        if custom_system else default_system
    )

    # Build full messages array: previous turns + current question with context
    current_prompt = (
        f"Question: {question}\n\n"
        f"Source documents:\n---\n{context}\n---\n\n"
        f"Answer concisely with citations."
    )
    messages = list(chat_history or [])
    messages.append({"role": "user", "content": current_prompt})

    yield sse_event("start", {"question": question, "sources_count": len(chunks)})

    full_text = ""
    # temperature=0.3: ドキュメントに基づく事実回答なので低め
    async for token in stream_chat(messages, system=system, temperature=0.3):
        full_text += token
        yield sse_event("token", {"text": token})

    citations = [
        {"doc_id": c.doc_id, "chunk_id": c.chunk_id, "page": c.page_number}
        for c in chunks
    ]
    yield sse_event("complete", {"citations": citations, "word_count": len(full_text.split())})
