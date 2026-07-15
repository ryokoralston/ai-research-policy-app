"""SQLite FTS5-backed lexical (BM25) search index.

Complements ChromaDB's semantic search: embeddings are good at conceptual
similarity but routinely miss exact identifiers (docket numbers like
"INC-2023-Q4-011", statute citations, acronyms) because those tokens carry
little distributional meaning. FTS5's bm25() ranking gives exact-term
matches proper weight.

This module owns a dedicated SQLite database file, separate from the main
app DB (models.DocumentChunk / database.py) and from ChromaDB's persist
dir — it is purely a search index and can always be rebuilt from the main
DB via scripts/build_bm25_index.py.

This index is fused with ChromaDB's semantic search results in
rag/retriever.py's Retriever.retrieve, via Reciprocal Rank Fusion
(rag/retriever.py's rrf_fuse).
"""
import os
import re
import sqlite3

from config import get_settings
from rag.vector_store import RetrievedChunk


class LexicalIndex:
    """BM25 lexical search over document chunks, backed by SQLite FTS5.

    Opens a fresh sqlite3 connection per operation rather than holding one
    open for the lifetime of the instance. This class is called from FastAPI
    background tasks (and from a standalone backfill script), where a
    long-lived connection would need its own thread-safety story; a
    short-lived connection per call sidesteps that entirely at a small,
    acceptable cost.
    """

    def __init__(self, db_path: str | None = None):
        settings = get_settings()
        self._db_path = db_path if db_path is not None else settings.bm25_index_path
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _ensure_schema(self) -> None:
        """Create (or migrate to) schema v2: chunk_fts2 adds display_content
        and context columns on top of the original chunk_fts columns, so the
        text FTS5 matches on (`content`, now the Contextual-Retrieval
        combined match text — see rag/contextualizer.py) can differ from the
        text shown to users (`display_content`, the original chunk text —
        citation snippets must never leak AI-generated situating context).

        If a legacy chunk_fts table exists (pre-Contextual-Retrieval
        deployments), its rows are copied into chunk_fts2 (display_content =
        content, context = '') and the legacy table is dropped — a user
        restarting the app before any backfill runs must keep working with
        zero data loss. Runs once: after migration chunk_fts no longer
        exists, so later __init__ calls see only chunk_fts2 and no-op here.
        """
        conn = self._connect()
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts2 USING fts5("
                "content, display_content UNINDEXED, context UNINDEXED, "
                "chunk_id UNINDEXED, doc_id UNINDEXED, "
                "page_number UNINDEXED, section_header UNINDEXED, "
                "chunk_index UNINDEXED)"
            )
            legacy_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_fts'"
            ).fetchone()
            if legacy_exists:
                conn.execute(
                    "INSERT INTO chunk_fts2 (content, display_content, context, "
                    "chunk_id, doc_id, page_number, section_header, chunk_index) "
                    "SELECT content, content, '', chunk_id, doc_id, page_number, "
                    "section_header, chunk_index FROM chunk_fts"
                )
                conn.execute("DROP TABLE chunk_fts")
            conn.commit()
        finally:
            conn.close()

    def add_chunks(
        self,
        chunk_ids: list[str],
        documents: list[str],
        metadatas: list[dict],
        display_documents: list[str] | None = None,
        contexts: list[str] | None = None,
    ) -> None:
        """Add chunks to the index. Same argument meaning as
        VectorStore.add_chunks, minus embeddings (lexical search needs no
        vector representation).

        documents: the text FTS5 matches against — for Contextual Retrieval
                   callers this is combine(context, original_content), for
                   pre-feature callers it's just the original content.
        display_documents: text returned to callers as RetrievedChunk.content
                   (citation snippets etc.) — defaults to `documents` when
                   omitted, preserving old callers' behavior byte-for-byte.
        contexts: the situating context alone, stored so callers can display
                   it separately (rag_service's "[Context: ...]" line) —
                   defaults to "" per chunk when omitted.
        """
        if display_documents is None:
            display_documents = documents
        if contexts is None:
            contexts = [""] * len(chunk_ids)
        rows = [
            (
                doc,
                display,
                context,
                chunk_id,
                meta.get("doc_id", ""),
                meta.get("page_number"),
                meta.get("section_header", ""),
                meta.get("chunk_index", 0),
            )
            for chunk_id, doc, display, context, meta in zip(
                chunk_ids, documents, display_documents, contexts, metadatas
            )
        ]
        conn = self._connect()
        try:
            conn.executemany(
                "INSERT INTO chunk_fts2 (content, display_content, context, chunk_id, "
                "doc_id, page_number, section_header, chunk_index) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _build_match_query(query: str) -> str | None:
        """Sanitize a raw user query into a safe FTS5 MATCH expression.

        Raw queries routinely break FTS5's MATCH syntax — hyphens, quotes,
        colons, parens, and bareword AND/OR/NOT are all significant to the
        query-string grammar. Tokenizing and re-quoting every token sidesteps
        that entirely: each token becomes a quoted string literal, which FTS5
        always treats as literal content regardless of what characters it
        contains.

        Tokens are OR-ed together rather than AND-ed: BM25 ranking already
        rewards documents that match more of the query, and AND would return
        zero results for any long natural-language question where not every
        chunk contains every word.

        Returns None if the query has no word tokens (empty or
        punctuation/whitespace-only), so callers can skip querying entirely.
        """
        tokens = re.findall(r"\w+", query.lower())
        if not tokens:
            return None
        return " OR ".join(f'"{t}"' for t in tokens)

    def search(
        self,
        query: str,
        n_results: int = 20,
        doc_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        match_query = self._build_match_query(query)
        if match_query is None:
            return []

        sql = (
            "SELECT chunk_id, doc_id, display_content, page_number, section_header, "
            "chunk_index, context, bm25(chunk_fts2) AS rank "
            "FROM chunk_fts2 WHERE chunk_fts2 MATCH ?"
        )
        params: list = [match_query]

        if doc_ids:
            placeholders = ", ".join("?" for _ in doc_ids)
            sql += f" AND doc_id IN ({placeholders})"
            params.extend(doc_ids)

        sql += " ORDER BY rank LIMIT ?"
        params.append(n_results)

        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        chunks = []
        for chunk_id, doc_id, display_content, page_number, section_header, chunk_index, context, rank in rows:
            chunks.append(RetrievedChunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                content=display_content,
                page_number=page_number,
                section_header=section_header,
                # FTS5's bm25() is lower-is-better (and negative); negate so
                # that, like VectorStore.query's score, higher always means
                # more relevant.
                score=-rank,
                chunk_index=chunk_index,
                context=context or "",
            ))
        return chunks

    def delete_document(self, doc_id: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM chunk_fts2 WHERE doc_id = ?", (doc_id,))
            conn.commit()
        finally:
            conn.close()

    def count(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT COUNT(*) FROM chunk_fts2").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def clear(self) -> None:
        """Delete all entries in the index (used by the backfill script to
        make reruns idempotent)."""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM chunk_fts2")
            conn.commit()
        finally:
            conn.close()
