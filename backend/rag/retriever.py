"""Three-stage retrieval: dual (vector + lexical) search, RRF fusion, and
cross-encoder reranking.

Vector search (semantic) and BM25 lexical search (rag/lexical_index.py) each
have blind spots the other covers: embeddings are strong on conceptual
similarity but routinely miss exact identifiers/citations, while BM25 is
strong on exact terms but blind to paraphrase. Reciprocal Rank Fusion merges
both candidate rankings into one pool before the (more expensive)
cross-encoder reranks the merged result.
"""
from functools import lru_cache
from typing import Any

from rag.lexical_index import LexicalIndex
from rag.vector_store import VectorStore, RetrievedChunk
from services.embedding_service import EmbeddingService


@lru_cache(maxsize=1)
def _load_reranker() -> Any:
    from sentence_transformers import CrossEncoder
    return CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")


def rrf_fuse(rankings: list[list[RetrievedChunk]], k: int = 60) -> list[RetrievedChunk]:
    """Merge multiple ranked candidate lists via Reciprocal Rank Fusion.

    Standard RRF: a chunk at 1-based rank r within one ranking list
    contributes 1 / (k + r) to its fused score; contributions are summed
    across all lists, keyed by chunk_id. Chunks that appear in only one
    ranking still get a score (just a single contribution).

    Deduplication: the first-seen RetrievedChunk instance for a given
    chunk_id is kept as the representative returned in the fused list —
    later occurrences of the same chunk_id (e.g. it appears in both the
    vector and lexical rankings) only affect the score, not which object
    is returned.

    Ordering: chunks are sorted by fused score descending. Ties are broken
    by first-seen order (the order chunk_ids were first encountered while
    scanning `rankings` left to right, top to bottom) so the result is
    deterministic across repeated calls with the same input.

    Score fields are intentionally left untouched: each input chunk's
    `.score` still means whatever its origin index gave it (cosine
    similarity for vector search, negated bm25() for lexical search) —
    those are not comparable across indices, so overwriting `.score` with
    the fused RRF value would silently conflate two different scales. The
    fusion result is carried entirely by the returned list's order, not by
    any field on the chunks themselves.

    Handles empty input gracefully: rrf_fuse([]) == [], rrf_fuse([[], []])
    == [], and rankings of different lengths are fine (rank is simply the
    1-based position within whichever list a chunk appears in).
    """
    fused_scores: dict[str, float] = {}
    representative: dict[str, RetrievedChunk] = {}
    first_seen_order: dict[str, int] = {}
    next_order = 0

    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            cid = chunk.chunk_id
            fused_scores[cid] = fused_scores.get(cid, 0.0) + 1.0 / (k + rank)
            if cid not in representative:
                representative[cid] = chunk
                first_seen_order[cid] = next_order
                next_order += 1

    ordered_ids = sorted(
        fused_scores.keys(),
        key=lambda cid: (-fused_scores[cid], first_seen_order[cid]),
    )
    return [representative[cid] for cid in ordered_ids]


class Retriever:
    def __init__(self):
        self._vs = VectorStore()
        self._embed = EmbeddingService()
        self._lexical = LexicalIndex()

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

        vector_candidates = self._vs.query(query_embedding, n_results=n_results, where=where)
        lexical_candidates = self._lexical.search(question, n_results=candidate_k, doc_ids=doc_ids)

        # RRF fusion: merge the two independently-ranked candidate lists into
        # one pool before reranking. See rrf_fuse's docstring for the scoring
        # rule; k=60 is the standard RRF constant.
        fused = rrf_fuse([vector_candidates, lexical_candidates], k=60)

        if not fused:
            return []

        # Cross-encoder reranking (model is loaded once and cached — a failed
        # load is not cached, so it is retried on the next query)
        try:
            reranker = _load_reranker()
            pairs = [(question, c.content) for c in fused]
            scores = reranker.predict(pairs)
            ranked = sorted(zip(fused, scores), key=lambda x: x[1], reverse=True)
            top = [chunk for chunk, _ in ranked[:top_k]]
        except Exception:
            # Fallback to the fused RRF order (an improvement over the old
            # vector-only fallback: it already reflects both semantic and
            # exact-term signal, not just cosine similarity).
            top = fused[:top_k]

        # Restore reading order: group the selected chunks by document, in their
        # original position within each document, so the excerpts read as
        # coherent passages. (Selection above is by relevance; only the final
        # presentation order changes here.)
        top.sort(key=lambda c: (c.doc_id, c.chunk_index))
        return top
