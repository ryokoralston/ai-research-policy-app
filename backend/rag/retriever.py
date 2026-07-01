"""Two-stage retrieval: vector search + cross-encoder reranking."""
from functools import lru_cache
from typing import Any

from rag.vector_store import VectorStore, RetrievedChunk
from services.embedding_service import EmbeddingService


@lru_cache(maxsize=1)
def _load_reranker() -> Any:
    from sentence_transformers import CrossEncoder
    return CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")


class Retriever:
    def __init__(self):
        self._vs = VectorStore()
        self._embed = EmbeddingService()

    def retrieve(
        self,
        question: str,
        top_k: int = 5,
        doc_ids: list[str] | None = None,
        candidate_k: int = 20,
    ) -> list[RetrievedChunk]:
        query_embedding = self._embed.embed_query(question)

        where = None
        if doc_ids:
            if len(doc_ids) == 1:
                where = {"doc_id": doc_ids[0]}
            else:
                where = {"doc_id": {"$in": doc_ids}}

        # Clamp candidate_k to available count
        available = self._vs.count()
        n_results = min(candidate_k, max(available, 1))

        candidates = self._vs.query(query_embedding, n_results=n_results, where=where)

        if not candidates:
            return []

        # Cross-encoder reranking (model is loaded once and cached — a failed
        # load is not cached, so it is retried on the next query)
        try:
            reranker = _load_reranker()
            pairs = [(question, c.content) for c in candidates]
            scores = reranker.predict(pairs)
            ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
            top = [chunk for chunk, _ in ranked[:top_k]]
        except Exception:
            # Fallback to vector similarity order
            top = candidates[:top_k]

        # Restore reading order: group the selected chunks by document, in their
        # original position within each document, so the excerpts read as
        # coherent passages. (Selection above is by relevance; only the final
        # presentation order changes here.)
        top.sort(key=lambda c: (c.doc_id, c.chunk_index))
        return top
