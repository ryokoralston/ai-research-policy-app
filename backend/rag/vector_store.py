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


class VectorStore:
    COLLECTION_NAME = "policy_documents"

    def __init__(self):
        settings = get_settings()
        self._client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
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
            ))
        return chunks

    def delete_document(self, doc_id: str) -> None:
        results = self._collection.get(where={"doc_id": doc_id})
        if results["ids"]:
            self._collection.delete(ids=results["ids"])

    def count(self) -> int:
        return self._collection.count()
