"""Rebuild the BM25 lexical (FTS5) index from all stored document chunks.

The SQLite `document_chunks` table (models.DocumentChunk) is the source of
truth for chunk content, so this script reads every row from there and
writes it into rag.lexical_index.LexicalIndex's dedicated FTS5 database.

Idempotent: the index is cleared first, so rerunning this script is always
safe (e.g. after adding lexical search to an existing deployment, or after
the FTS5 database is deleted/corrupted).

Run from the backend/ directory:
    ./venv/bin/python -m scripts.build_bm25_index
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from database import SessionLocal
from models import DocumentChunk
from rag.contextualizer import combine
from rag.lexical_index import LexicalIndex
from rag.vector_store import VectorStore

BATCH_SIZE = 500


def build() -> None:
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
        print("No document chunks found in the database. Nothing to index.")
        return

    # Contexts live only in Chroma metadata (SQLite stores original content
    # only), so pull them from there before rebuilding — otherwise a rebuild
    # after a contextualize_reindex backfill would silently drop every
    # context from the lexical index's match text. Same preservation
    # approach as scripts/reindex_embeddings.py.
    try:
        contexts_by_id = VectorStore().get_contexts()
    except Exception as exc:
        print(f"Could not read contexts from ChromaDB ({exc}); indexing without contexts.")
        contexts_by_id = {}

    lexical = LexicalIndex()

    print(f"Rebuilding BM25 index from {len(chunks)} chunks")

    lexical.clear()
    print("Cleared existing contents of the BM25 index.")

    total_batches = (len(chunks) + BATCH_SIZE - 1) // BATCH_SIZE
    indexed_count = 0

    for batch_num, i in enumerate(range(0, len(chunks), BATCH_SIZE), start=1):
        batch = chunks[i:i + BATCH_SIZE]

        batch_ids = [c.chroma_id or c.id for c in batch]
        batch_contexts = [contexts_by_id.get(cid, "") for cid in batch_ids]
        lexical.add_chunks(
            chunk_ids=batch_ids,
            documents=[combine(ctx, c.content) for ctx, c in zip(batch_contexts, batch)],
            display_documents=[c.content for c in batch],
            contexts=batch_contexts,
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

        indexed_count += len(batch)
        print(f"  batch {batch_num}/{total_batches}: indexed {indexed_count}/{len(chunks)} chunks")

    final_count = lexical.count()
    print("\nDone.")
    print(f"  chunks read from SQLite: {len(chunks)}")
    print(f"  chunks written to BM25 index: {indexed_count}")
    print(f"  index count after rebuild: {final_count}")


if __name__ == "__main__":
    build()
