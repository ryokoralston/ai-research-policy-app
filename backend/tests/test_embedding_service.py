"""Tests for services/embedding_service.py — provider selection, collection
naming, VoyageAI batching/input_type behavior, and local query prefixing.

No network calls and no real model loads: the local sentence-transformer
model is monkeypatched out, and the Voyage REST layer is exercised via an
injected fake post function.

Run from the backend directory:
    ./venv/bin/python -m tests.test_embedding_service
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import services.embedding_service as es
from services.embedding_service import (
    EmbeddingService,
    LocalEmbeddingProvider,
    VoyageEmbeddingProvider,
    VOYAGE_BATCH_SIZE,
)


class _FakeSettings:
    def __init__(self, voyage_api_key: str = "", voyage_embedding_model: str = "voyage-3-large"):
        self.voyage_api_key = voyage_api_key
        self.voyage_embedding_model = voyage_embedding_model


class _FakePost:
    """Records every REST payload so tests can assert on batching/input_type.

    Mimics the Voyage embeddings response shape ({"data": [{"embedding",
    "index"}]}); items are returned in REVERSED index order to prove the
    provider sorts by "index" instead of trusting response order.
    """

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, api_key, payload):
        texts = list(payload["input"])
        self.calls.append({
            "api_key": api_key,
            "texts": texts,
            "model": payload["model"],
            "input_type": payload["input_type"],
        })
        # Embedding value encodes the source text's length so callers can
        # verify per-item ordering survives batching/concatenation.
        return {"data": [
            {"index": i, "embedding": [float(len(t))]}
            for i, t in reversed(list(enumerate(texts)))
        ]}


class _FakeArray:
    """Mimics the numpy array returned by SentenceTransformer.encode()."""

    def __init__(self, data):
        self._data = data

    def tolist(self):
        return self._data


class _FakeLocalModel:
    def __init__(self):
        self.calls: list[list[str]] = []

    def encode(self, texts, show_progress_bar=False):
        self.calls.append(list(texts))
        return _FakeArray([[1.0, 2.0, 3.0] for _ in texts])


def _with_fake_settings(fake_settings):
    """Context-manager-less monkeypatch of embedding_service.get_settings."""
    original = es.get_settings
    es.get_settings = lambda: fake_settings
    return original


def _restore_settings(original):
    es.get_settings = original


# ── Provider selection ──────────────────────────────────────────────────────

def test_provider_selection_voyage_when_key_set():
    original = _with_fake_settings(_FakeSettings(voyage_api_key="fake-key"))
    try:
        service = EmbeddingService()
        assert isinstance(service._provider, VoyageEmbeddingProvider), type(service._provider)
    finally:
        _restore_settings(original)


def test_provider_selection_local_when_no_key():
    original = _with_fake_settings(_FakeSettings(voyage_api_key=""))
    try:
        service = EmbeddingService()
        assert isinstance(service._provider, LocalEmbeddingProvider), type(service._provider)
    finally:
        _restore_settings(original)


# ── Collection naming (backward compat) ─────────────────────────────────────

def test_collection_name_local_is_unchanged_legacy_name():
    original = _with_fake_settings(_FakeSettings(voyage_api_key=""))
    try:
        service = EmbeddingService()
        assert service.collection_name == "policy_documents", service.collection_name
    finally:
        _restore_settings(original)


def test_collection_name_voyage_is_namespaced_by_model():
    original = _with_fake_settings(
        _FakeSettings(voyage_api_key="fake-key", voyage_embedding_model="voyage-3-large")
    )
    try:
        service = EmbeddingService()
        assert service.collection_name == "policy_documents__voyage-3-large", service.collection_name
    finally:
        _restore_settings(original)


# ── VoyageEmbeddingProvider: input_type + batching ──────────────────────────

def test_voyage_embed_texts_uses_document_input_type():
    post = _FakePost()
    provider = VoyageEmbeddingProvider(model="voyage-3-large", api_key="fake-key", post=post)
    result = provider.embed_texts(["a", "bb", "ccc"])
    assert len(post.calls) == 1, len(post.calls)
    assert post.calls[0]["input_type"] == "document"
    assert post.calls[0]["model"] == "voyage-3-large"
    assert post.calls[0]["api_key"] == "fake-key"
    # _FakePost returns items in reversed index order — this passing proves
    # the provider sorts by "index" instead of trusting response order.
    assert result == [[1.0], [2.0], [3.0]], result


def test_voyage_embed_query_uses_query_input_type_single_text():
    post = _FakePost()
    provider = VoyageEmbeddingProvider(model="voyage-3-large", api_key="fake-key", post=post)
    result = provider.embed_query("hello")
    assert len(post.calls) == 1, len(post.calls)
    assert post.calls[0]["input_type"] == "query"
    assert post.calls[0]["texts"] == ["hello"]
    assert result == [5.0], result  # len("hello") == 5


def test_voyage_embed_texts_batches_at_128_and_concatenates_in_order():
    post = _FakePost()
    provider = VoyageEmbeddingProvider(model="voyage-3-large", api_key="fake-key", post=post)
    texts = [f"chunk-{i}" for i in range(300)]

    result = provider.embed_texts(texts)

    assert len(post.calls) == 3, len(post.calls)
    sizes = [len(c["texts"]) for c in post.calls]
    assert sizes == [128, 128, 44], sizes
    assert all(size <= VOYAGE_BATCH_SIZE for size in sizes)

    # Order preserved end-to-end: result[i] must correspond to texts[i].
    assert len(result) == 300, len(result)
    assert result == [[float(len(t))] for t in texts]

    # Batches themselves preserve input order (no shuffling within a batch).
    assert post.calls[0]["texts"] == texts[0:128]
    assert post.calls[1]["texts"] == texts[128:256]
    assert post.calls[2]["texts"] == texts[256:300]


# ── model_slug sanitization ─────────────────────────────────────────────────

def test_model_slug_sanitization():
    provider = VoyageEmbeddingProvider(model="voyage-3-large", api_key="fake-key", post=_FakePost())
    assert provider.model_slug == "voyage-3-large", provider.model_slug

    mixed_case_and_space = VoyageEmbeddingProvider(model="Voyage 3 Large", api_key="fake-key", post=_FakePost())
    assert mixed_case_and_space.model_slug == "voyage-3-large", mixed_case_and_space.model_slug

    with_slash = VoyageEmbeddingProvider(model="Voyage/3-Large", api_key="fake-key", post=_FakePost())
    assert with_slash.model_slug == "voyage-3-large", with_slash.model_slug

    import re
    assert re.fullmatch(r"[a-z0-9-]+", mixed_case_and_space.model_slug)
    assert re.fullmatch(r"[a-z0-9-]+", with_slash.model_slug)


# ── LocalEmbeddingProvider: "query: " prefix, no network/model download ────

def test_local_embed_query_applies_prefix():
    fake_model = _FakeLocalModel()
    original_load_model = es._load_model
    es._load_model = lambda: fake_model
    try:
        provider = LocalEmbeddingProvider()
        embedding = provider.embed_query("what is the policy?")
        assert fake_model.calls == [["query: what is the policy?"]], fake_model.calls
        assert embedding == [1.0, 2.0, 3.0], embedding
    finally:
        es._load_model = original_load_model


def test_local_embed_texts_does_not_apply_query_prefix():
    fake_model = _FakeLocalModel()
    original_load_model = es._load_model
    es._load_model = lambda: fake_model
    try:
        provider = LocalEmbeddingProvider()
        provider.embed_texts(["doc one", "doc two"])
        assert fake_model.calls == [["doc one", "doc two"]], fake_model.calls
    finally:
        es._load_model = original_load_model


def test_local_provider_model_slug():
    assert LocalEmbeddingProvider().model_slug == "minilm-l6-v2"


# ── Test runner ───────────────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")


if __name__ == "__main__":
    print("\nRunning embedding_service tests...\n")

    _run("provider selection: voyage when key set", test_provider_selection_voyage_when_key_set)
    _run("provider selection: local when no key", test_provider_selection_local_when_no_key)

    _run("collection_name: local is unchanged legacy name", test_collection_name_local_is_unchanged_legacy_name)
    _run("collection_name: voyage is namespaced by model", test_collection_name_voyage_is_namespaced_by_model)

    _run("voyage embed_texts uses document input_type", test_voyage_embed_texts_uses_document_input_type)
    _run("voyage embed_query uses query input_type, single text", test_voyage_embed_query_uses_query_input_type_single_text)
    _run("voyage embed_texts batches at 128 and concatenates in order", test_voyage_embed_texts_batches_at_128_and_concatenates_in_order)

    _run("model_slug sanitization", test_model_slug_sanitization)

    _run("local embed_query applies 'query: ' prefix", test_local_embed_query_applies_prefix)
    _run("local embed_texts does not apply query prefix", test_local_embed_texts_does_not_apply_query_prefix)
    _run("local provider model_slug", test_local_provider_model_slug)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
