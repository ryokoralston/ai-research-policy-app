"""Embedding providers: local sentence-transformers (default, no API key) and
VoyageAI (optional, opt-in via VOYAGE_API_KEY).

Collections are namespaced per embedding model (see EmbeddingService.collection_name)
because embeddings from different models have different dimensions and live in
different vector spaces — mixing them in one ChromaDB collection breaks search
silently (near-neighbor results become meaningless) and Chroma outright errors
on a dimension mismatch once a collection has been seeded by another model.
"""
import re
from functools import lru_cache
from typing import Any, Callable, Protocol

from config import get_settings

# Voyage API batch limit: at most 128 texts per embeddings request.
VOYAGE_BATCH_SIZE = 128

VOYAGE_EMBEDDINGS_URL = "https://api.voyageai.com/v1/embeddings"


def _voyage_post(api_key: str, payload: dict) -> dict:
    """POST to the Voyage embeddings REST endpoint and return the parsed JSON.

    We call the REST API directly with httpx instead of the official voyageai
    SDK: the SDK drags in langchain-core/langsmith as transitive dependencies,
    which this project deliberately keeps out of its dependency tree.
    """
    import httpx

    resp = httpx.post(
        VOYAGE_EMBEDDINGS_URL,
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


@lru_cache(maxsize=1)
def _load_model() -> Any:
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")


class EmbeddingProvider(Protocol):
    """Interface implemented by each concrete embedding backend."""

    model_slug: str

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, query: str) -> list[float]: ...


class LocalEmbeddingProvider:
    """Local sentence-transformer embeddings (no API key required).

    This preserves today's exact behavior: same model, same lru_cache'd load,
    same "query: " prefix convention for queries (MSMARCO fine-tuning style).
    """

    model_slug = "minilm-l6-v2"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        model = _load_model()
        embeddings = model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    def embed_query(self, query: str) -> list[float]:
        # MSMARCO fine-tuning convention: prefix query with "query: "
        return self.embed_texts([f"query: {query}"])[0]


class VoyageEmbeddingProvider:
    """VoyageAI embeddings. Distinguishes query vs. document embeddings via
    Voyage's native `input_type` parameter — no manual prefix hacks needed,
    unlike the local provider's "query: " convention.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        post: Callable[[str, dict], dict] | None = None,
    ):
        settings = get_settings()
        self._model = model or settings.voyage_embedding_model
        self._api_key = api_key or settings.voyage_api_key
        self.model_slug = re.sub(r"[^a-z0-9-]", "-", self._model.lower())
        self._post = post or _voyage_post  # injectable for tests

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        data = self._post(self._api_key, {
            "input": texts,
            "model": self._model,
            "input_type": input_type,
        })
        # Response items carry an "index" into the request batch — sort by it
        # rather than trusting response order.
        items = sorted(data["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in items]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for i in range(0, len(texts), VOYAGE_BATCH_SIZE):
            embeddings.extend(self._embed(texts[i:i + VOYAGE_BATCH_SIZE], "document"))
        return embeddings

    def embed_query(self, query: str) -> list[float]:
        return self._embed([query], "query")[0]


class EmbeddingService:
    """Facade over the active embedding provider.

    Picks the provider once at construction: VoyageAI if VOYAGE_API_KEY is
    set, otherwise the local sentence-transformer model (today's default
    behavior, unchanged when no key is present).
    """

    def __init__(self):
        settings = get_settings()
        if settings.voyage_api_key:
            self._provider: EmbeddingProvider = VoyageEmbeddingProvider()
        else:
            self._provider = LocalEmbeddingProvider()

    @property
    def collection_name(self) -> str:
        # Backward compat: the local provider keeps mapping to the original
        # "policy_documents" collection so existing indexes keep working.
        if isinstance(self._provider, LocalEmbeddingProvider):
            return "policy_documents"
        return f"policy_documents__{self._provider.model_slug}"

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self._provider.embed_texts(texts)

    def embed_query(self, query: str) -> list[float]:
        return self._provider.embed_query(query)
