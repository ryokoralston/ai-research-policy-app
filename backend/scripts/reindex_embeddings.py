"""Re-embed all stored document chunks into the ACTIVE embedding provider's
ChromaDB collection.

Use this after switching embedding providers (e.g. setting VOYAGE_API_KEY):
the SQLite `document_chunks` table is the source of truth for chunk content,
so this script re-embeds every chunk's `content` with whichever provider
`EmbeddingService` currently selects, and writes into that provider's
collection (see services/embedding_service.py's collection_name property).

Idempotent: the target collection's existing contents are cleared first, so
rerunning this script is always safe.

Run from the backend/ directory:
    ./venv/bin/python -m scripts.reindex_embeddings
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from database import SessionLocal
from models import DocumentChunk
from rag.vector_store import VectorStore
from services.embedding_service import EmbeddingService

BATCH_SIZE = 100


def reindex() -> None:
    db = SessionLocal()
    try:
        chunks = (
            db.query(DocumentChunk)
            .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
            .all()
        )
    finally:
        db.close()

    if not chunks:
        print("No document chunks found in the database. Nothing to reindex.")
        return

    embed_service = EmbeddingService()
    vs = VectorStore()

    print(f"Reindexing {len(chunks)} chunks into collection '{embed_service.collection_name}'")

    vs.clear()
    print("Cleared existing contents of the target collection.")

    total_batches = (len(chunks) + BATCH_SIZE - 1) // BATCH_SIZE
    embedded_count = 0

    for batch_num, i in enumerate(range(0, len(chunks), BATCH_SIZE), start=1):
        batch = chunks[i:i + BATCH_SIZE]
        texts = [c.content for c in batch]
        embeddings = embed_service.embed_texts(texts)

        vs.add_chunks(
            chunk_ids=[c.chroma_id or c.id for c in batch],
            embeddings=embeddings,
            documents=texts,
            metadatas=[
                {
                    "doc_id": c.document_id,
                    "page_number": c.page_number,
                    "section_header": c.section_header or "",
                    "chunk_index": c.chunk_index,
                }
                for c in batch
            ],
        )

        embedded_count += len(batch)
        print(f"  batch {batch_num}/{total_batches}: embedded {embedded_count}/{len(chunks)} chunks")

    final_count = vs.count()
    print("\nDone.")
    print(f"  chunks read from SQLite: {len(chunks)}")
    print(f"  chunks written to Chroma: {embedded_count}")
    print(f"  collection count after reindex: {final_count}")


if __name__ == "__main__":
    reindex()
