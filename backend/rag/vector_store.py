"""ChromaDB vector store wrapper."""
from dataclasses import dataclass

import chromadb

from config import get_settings


@dataclass
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    content: str
    page_number: int | None
    section_header: str | None
    score: float
    chunk_index: int = 0  # position within the document (for reading order)
    # AI-generated situating context (Contextual Retrieval — see
    # rag/contextualizer.py). "" when the feature is off or generation
    # failed for this chunk. Never part of `content` (the citation/display
    # contract) — surfaced separately so callers can show it alongside the
    # excerpt (see rag_service.py's "[Context: ...]" line).
    context: str = ""


class VectorStore:
    # Legacy default, kept only as a fallback / reference; the collection
    # actually used is determined per active embedding provider (see below).
    COLLECTION_NAME = "policy_documents"

    def __init__(self, collection_name: str | None = None):
        settings = get_settings()
        if collection_name is None:
            # Import inside __init__ to avoid a module-level import cycle
            # (services.embedding_service does not import rag.vector_store,
            # but keeping this local documents the intent and is cheap).
            from services.embedding_service import EmbeddingService
            collection_name = EmbeddingService().collection_name
        self._client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(
        self,
        chunk_ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        self._collection.add(
            ids=chunk_ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def query(
        self,
        query_embedding: list[float],
        n_results: int = 20,
        where: dict | None = None,
    ) -> list[RetrievedChunk]:
        kwargs: dict = {
            "query_embeddings": [query_embedding],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = self._collection.query(**kwargs)

        chunks = []
        ids = results["ids"][0]
        docs = results["documents"][0]  # type: ignore
        metas = results["metadatas"][0]  # type: ignore
        distances = results["distances"][0]  # type: ignore

        for chunk_id, doc, meta, dist in zip(ids, docs, metas, distances):
            chunks.append(RetrievedChunk(
                chunk_id=chunk_id,
                doc_id=meta.get("doc_id", ""),
                content=doc,
                page_number=meta.get("page_number"),
                section_header=meta.get("section_header"),
                score=1.0 - dist,  # cosine: distance → similarity
                chunk_index=meta.get("chunk_index", 0),
                context=meta.get("context") or "",
            ))
        return chunks

    def delete_document(self, doc_id: str) -> None:
        results = self._collection.get(where={"doc_id": doc_id})
        if results["ids"]:
            self._collection.delete(ids=results["ids"])

    def count(self) -> int:
        return self._collection.count()

    def get_contexts(self) -> dict[str, str]:
        """Map every chunk_id currently in the collection to its stored
        "context" metadata (Contextual Retrieval — see
        rag/contextualizer.py). Missing/empty context maps to "".

        Used by scripts/reindex_embeddings.py to preserve contexts across a
        from-scratch re-embed (that script re-embeds raw DocumentChunk
        content and would otherwise silently drop any already-generated
        contexts when it clears and rewrites the collection).
        """
        existing = self._collection.get(include=["metadatas"])
        return {
            chunk_id: (meta.get("context") or "")
            for chunk_id, meta in zip(existing["ids"], existing["metadatas"])
        }

    def clear(self) -> None:
        """Delete all entries in this collection (used by the reindex script
        to make reruns idempotent)."""
        existing = self._collection.get(include=[])
        if existing["ids"]:
            self._collection.delete(ids=existing["ids"])
