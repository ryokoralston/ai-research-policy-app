"""Backfill Contextual Retrieval (rag/contextualizer.py) onto already-indexed
documents: generate a situating context for every existing chunk and
re-embed/re-lex with combine(context, content) as the match text.

Idempotent and chunk-id-preserving: for each document, existing chunk_ids
(DocumentChunk.chroma_id or .id — the SAME ids already used in Chroma and the
FTS5 index) are re-added after a delete_document, so no id is invented and no
SQLite DocumentChunk row is touched. This matters because the main session's
retrieval A/B eval (scripts/eval_retrieval_ab.py) treats chunk_id as ground
truth — rerunning this script must never invalidate a prior eval's questions.

Only Chroma + the lexical (BM25) index are rewritten; the SQLite
`document_chunks` table (content, page_number, section_header, chunk_index)
is read-only here.

Run from the backend/ directory:
    ./venv/bin/python -m scripts.contextualize_reindex [--doc-id ID] [--dry-run]

Not run against real data by this task — see the calling agent's instructions.
"""
import argparse
import asyncio
import os
import sys
import time

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from database import SessionLocal
from models import Document, DocumentChunk
from rag.contextualizer import contextualize_chunks, combine
from rag.lexical_index import LexicalIndex
from rag.vector_store import VectorStore
from services.embedding_service import EmbeddingService


def _load_documents(doc_id: str | None) -> list[Document]:
    db = SessionLocal()
    try:
        query = db.query(Document).filter(Document.status == "indexed")
        if doc_id:
            query = query.filter(Document.id == doc_id)
        return query.order_by(Document.created_at).all()
    finally:
        db.close()


def _load_chunks(document_id: str) -> list[DocumentChunk]:
    db = SessionLocal()
    try:
        return (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )
    finally:
        db.close()


async def _contextualize_one_document(doc: Document, embed_service: EmbeddingService, vs: VectorStore, lexical: LexicalIndex) -> int:
    """Regenerate contexts + re-embed/re-lex one document's chunks in place.
    Returns the number of chunks processed."""
    chunks = _load_chunks(doc.id)
    if not chunks:
        return 0

    chunk_ids = [c.chroma_id or c.id for c in chunks]
    contents = [c.content for c in chunks]
    full_text = "\n\n".join(contents)

    contexts = await contextualize_chunks(full_text, contents)
    combined = [combine(ctx, content) for ctx, content in zip(contexts, contents)]
    embeddings = embed_service.embed_texts(combined)

    metadatas = [
        {
            "doc_id": doc.id,
            "page_number": c.page_number,
            "section_header": c.section_header or "",
            "chunk_index": c.chunk_index,
            "context": ctx,
        }
        for c, ctx in zip(chunks, contexts)
    ]

    # Delete-then-readd with the SAME chunk_ids — see module docstring on why
    # ids must be preserved (the A/B eval's chunk_id ground truth).
    vs.delete_document(doc.id)
    vs.add_chunks(
        chunk_ids=chunk_ids,
        embeddings=embeddings,
        documents=contents,  # original content — citation/display contract
        metadatas=metadatas,
    )

    lexical.delete_document(doc.id)
    lexical.add_chunks(
        chunk_ids=chunk_ids,
        documents=combined,
        metadatas=metadatas,
        display_documents=contents,
        contexts=contexts,
    )

    return len(chunks)


async def run(doc_id: str | None, dry_run: bool) -> None:
    documents = _load_documents(doc_id)
    if not documents:
        print("No indexed documents found. Nothing to do.")
        return

    print(f"Found {len(documents)} indexed document(s) to process.")

    if dry_run:
        total_chunks = 0
        for i, doc in enumerate(documents, start=1):
            chunks = _load_chunks(doc.id)
            title = doc.title or doc.filename
            print(f"  [dry-run] {i}/{len(documents)}: {title!r} — {len(chunks)} chunks")
            total_chunks += len(chunks)
        print(f"\nDry run complete. {len(documents)} documents, {total_chunks} chunks total. No API calls made.")
        return

    embed_service = EmbeddingService()
    vs = VectorStore()
    lexical = LexicalIndex()

    start = time.monotonic()
    total_chunks = 0

    for i, doc in enumerate(documents, start=1):
        title = doc.title or doc.filename
        doc_start = time.monotonic()
        n_chunks = await _contextualize_one_document(doc, embed_service, vs, lexical)
        elapsed = time.monotonic() - doc_start
        print(f"  {i}/{len(documents)}: {title!r} — {n_chunks} chunks ({elapsed:.1f}s)")
        total_chunks += n_chunks

    total_elapsed = time.monotonic() - start
    print("\nDone.")
    print(f"  documents processed: {len(documents)}")
    print(f"  chunks processed: {total_chunks}")
    print(f"  total elapsed: {total_elapsed:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc-id", default=None, help="Only backfill this document id")
    parser.add_argument("--dry-run", action="store_true", help="Print per-document chunk counts without calling the API")
    args = parser.parse_args()
    asyncio.run(run(args.doc_id, args.dry_run))


if __name__ == "__main__":
    main()
