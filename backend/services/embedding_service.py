"""Local sentence-transformer embeddings (no API key required)."""
from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def _load_model() -> Any:
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


class EmbeddingService:
    """Wraps sentence-transformers for local embedding generation."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        model = _load_model()
        embeddings = model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    def embed_query(self, query: str) -> list[float]:
        # MSMARCO fine-tuning convention: prefix query with "query: "
        return self.embed_texts([f"query: {query}"])[0]
