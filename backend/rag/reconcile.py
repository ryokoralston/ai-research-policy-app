"""Reconcile the two search indexes against the database that backs them.

THE INVARIANT
-------------
No ChromaDB entry and no BM25 (SQLite FTS5) entry may exist without a backing
`document_chunks` row. An index entry whose id has no such row is an ORPHAN:
it can still be retrieved and cited, but the chunk it claims to quote no
longer exists anywhere in the app, so nothing can verify it.

The id both indexes are keyed by is `COALESCE(document_chunks.chroma_id,
document_chunks.id)` — the same expression scripts/reindex_embeddings.py uses
when it writes them (`c.chroma_id or c.id`). This module is the read side of
that contract.

HOW ORPHANS GET CREATED
-----------------------
1. Index cleanup failing during a document delete. routers/documents.py's
   delete_document used to clean Chroma and BM25 "best effort" — it logged the
   failure and deleted the DB rows anyway, stranding the index entries. That
   endpoint now cleans the indexes FIRST and refuses to delete the rows if
   either cleanup fails, so this path can no longer produce orphans.
2. Rows deleted directly in SQL, which bypasses the endpoint entirely. This is
   what actually happened on 2026-08-19, and no amount of endpoint hardening
   can prevent it — hence this module and scripts/reconcile_indexes.py.

DELIBERATELY NOT AUTOMATIC
--------------------------
find_orphans is read-only and delete_orphans is only ever called from the CLI
with an explicit --apply. Nothing sweeps orphans on a schedule or at startup:
the orphan definition treats the database as the source of truth, so if the
database were ever empty or pointed at the wrong file while the indexes were
intact, an automatic sweep would read "every entry is an orphan" and erase
both indexes. That is not hypothetical — this app has already lost its Render
env vars once, DATABASE_URL included.

This module must not pull in the embedding model: constructing VectorStore()
only reads EmbeddingService().collection_name, and services/embedding_service
loads sentence-transformers lazily inside _load_model(). Keep it that way —
main.py runs the orphan check at startup and must not pay for torch.
"""
from sqlalchemy.orm import Session

from models import DocumentChunk


def _valid_chunk_ids(db: Session) -> set[str]:
    """Every id the indexes are allowed to hold, per the DB.

    COALESCE(chroma_id, id) — matches how scripts/reindex_embeddings.py and
    services/rag_service.py choose the id they write into both indexes.
    """
    rows = db.query(DocumentChunk.id, DocumentChunk.chroma_id).all()
    return {(chroma_id or chunk_id) for chunk_id, chroma_id in rows}


def find_orphans(
    db: Session,
    vector_store=None,
    lexical_index=None,
) -> dict:
    """Report index entries that have no backing `document_chunks` row.

    Read-only: nothing is deleted, in either index or the database.

    vector_store / lexical_index are injection points for tests (and for
    callers that already hold an instance). Left as None, the real stores are
    constructed here — VectorStore() does not load an embedding model, only
    the collection name, so this stays cheap enough for a startup check.

    Returns:
        chroma_orphans / bm25_orphans: sorted ids present in that store with
            no chunk row. These are exactly the ids delete_orphans removes.
        chroma_total / bm25_total: entry counts in each store (bm25_total
            counts ROWS, which can exceed distinct ids — see
            LexicalIndex.list_chunk_ids).
        sqlite_chunk_ids: how many distinct ids the database says should exist.
    """
    if vector_store is None:
        from rag.vector_store import VectorStore
        vector_store = VectorStore()
    if lexical_index is None:
        from rag.lexical_index import LexicalIndex
        lexical_index = LexicalIndex()

    valid = _valid_chunk_ids(db)

    chroma_ids = vector_store.list_ids()
    bm25_ids = lexical_index.list_chunk_ids()

    return {
        "chroma_orphans": sorted(cid for cid in chroma_ids if cid not in valid),
        "bm25_orphans": sorted(cid for cid in bm25_ids if cid not in valid),
        "chroma_total": vector_store.count(),
        "bm25_total": lexical_index.count(),
        "sqlite_chunk_ids": len(valid),
    }


def delete_orphans(
    db: Session,
    vector_store=None,
    lexical_index=None,
) -> dict:
    """Delete exactly the ids find_orphans reports, and report what went.

    Destructive — only ever reached through scripts/reconcile_indexes.py
    --apply. Read the module docstring on why this is never automatic.

    The counts returned are what each store actually removed, which can differ
    from the number of orphan ids: BM25 removal counts rows (a duplicated
    chunk_id costs more than one row), while Chroma removal counts ids.
    """
    if vector_store is None:
        from rag.vector_store import VectorStore
        vector_store = VectorStore()
    if lexical_index is None:
        from rag.lexical_index import LexicalIndex
        lexical_index = LexicalIndex()

    report = find_orphans(db, vector_store=vector_store, lexical_index=lexical_index)

    chroma_removed = vector_store.delete_by_ids(report["chroma_orphans"])
    bm25_removed = lexical_index.delete_by_chunk_ids(report["bm25_orphans"])

    return {
        "chroma_removed": chroma_removed,
        "bm25_removed": bm25_removed,
        "chroma_total_after": vector_store.count(),
        "bm25_total_after": lexical_index.count(),
        "sqlite_chunk_ids": report["sqlite_chunk_ids"],
    }
