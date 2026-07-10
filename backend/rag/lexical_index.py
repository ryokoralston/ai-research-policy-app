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

This is index infrastructure only: nothing in rag/retriever.py calls into
this yet. Hybrid merging of lexical + vector results is a future change.
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
        conn = self._connect()
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5("
                "content, chunk_id UNINDEXED, doc_id UNINDEXED, "
                "page_number UNINDEXED, section_header UNINDEXED, "
                "chunk_index UNINDEXED)"
            )
            conn.commit()
        finally:
            conn.close()

    def add_chunks(
        self,
        chunk_ids: list[str],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        """Add chunks to the index. Same argument meaning as
        VectorStore.add_chunks, minus embeddings (lexical search needs no
        vector representation)."""
        rows = [
            (
                doc,
                chunk_id,
                meta.get("doc_id", ""),
                meta.get("page_number"),
                meta.get("section_header", ""),
                meta.get("chunk_index", 0),
            )
            for chunk_id, doc, meta in zip(chunk_ids, documents, metadatas)
        ]
        conn = self._connect()
        try:
            conn.executemany(
                "INSERT INTO chunk_fts (content, chunk_id, doc_id, page_number, "
                "section_header, chunk_index) VALUES (?, ?, ?, ?, ?, ?)",
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
            "SELECT chunk_id, doc_id, content, page_number, section_header, "
            "chunk_index, bm25(chunk_fts) AS rank "
            "FROM chunk_fts WHERE chunk_fts MATCH ?"
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
        for chunk_id, doc_id, content, page_number, section_header, chunk_index, rank in rows:
            chunks.append(RetrievedChunk(
                chunk_id=chunk_id,
                doc_id=doc_id,
                content=content,
                page_number=page_number,
                section_header=section_header,
                # FTS5's bm25() is lower-is-better (and negative); negate so
                # that, like VectorStore.query's score, higher always means
                # more relevant.
                score=-rank,
                chunk_index=chunk_index,
            ))
        return chunks

    def delete_document(self, doc_id: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM chunk_fts WHERE doc_id = ?", (doc_id,))
            conn.commit()
        finally:
            conn.close()

    def count(self) -> int:
        conn = self._connect()
        try:
            row = conn.execute("SELECT COUNT(*) FROM chunk_fts").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def clear(self) -> None:
        """Delete all entries in the index (used by the backfill script to
        make reruns idempotent)."""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM chunk_fts")
            conn.commit()
        finally:
            conn.close()
