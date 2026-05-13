"""
Anthropic / OpenAI wrapper with automatic provider routing.
Provides both streaming (SSE) and non-streaming text generation.
Model defaults are loaded from DB (ModelSettings) with a 60-second cache.
"""
import json
import time
from typing import AsyncIterator

import anthropic

from config import get_settings

# ── DB settings cache ─────────────────────────────────────────────────────────
_cache: dict | None = None
_cache_ts: float = 0


def _load_ai_settings() -> dict:
    """Load ModelSettings from DB with a 60-second TTL cache."""
    global _cache, _cache_ts
    if _cache is not None and time.time() - _cache_ts < 60:
        return _cache
    try:
        from database import SessionLocal, get_or_init_model_settings
        with SessionLocal() as db:
            ms = get_or_init_model_settings(db)
            _cache = {
                "main_model": ms.main_model,
                "fast_model": ms.fast_model,
                "anthropic_api_key": ms.anthropic_api_key,
                "openai_api_key": ms.openai_api_key,
            }
            _cache_ts = time.time()
    except Exception:
        # Fallback to config if DB is not available yet
        settings = get_settings()
        _cache = {
            "main_model": settings.claude_model,
            "fast_model": settings.claude_fast_model,
            "anthropic_api_key": settings.anthropic_api_key,
            "openai_api_key": "",
        }
        _cache_ts = time.time()
    return _cache


def invalidate_ai_settings_cache() -> None:
    global _cache_ts
    _cache_ts = 0


def _is_openai(model: str) -> bool:
    return model.startswith(("gpt-", "o1", "o3", "o4"))


# ── Client factories ──────────────────────────────────────────────────────────

def _get_anthropic_client(ai_settings: dict) -> anthropic.AsyncAnthropic:
    key = ai_settings.get("anthropic_api_key") or get_settings().anthropic_api_key
    return anthropic.AsyncAnthropic(api_key=key)


def _get_openai_client(ai_settings: dict):
    import openai as _openai
    key = ai_settings.get("openai_api_key") or ""
    return _openai.AsyncOpenAI(api_key=key)


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_text(
    prompt: str,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """Non-streaming text generation. Returns full response text."""
    ai_settings = _load_ai_settings()
    model = model or ai_settings["fast_model"]

    if _is_openai(model):
        client = _get_openai_client(ai_settings)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return response.choices[0].message.content or ""

    client = _get_anthropic_client(ai_settings)
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    message = await client.messages.create(**kwargs)
    return message.content[0].text  # type: ignore[union-attr]


async def stream_text(
    prompt: str,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 8192,
) -> AsyncIterator[str]:
    """Streaming text generation. Yields text tokens as they arrive."""
    ai_settings = _load_ai_settings()
    model = model or ai_settings["main_model"]

    if _is_openai(model):
        client = _get_openai_client(ai_settings)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        stream = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
        return

    client = _get_anthropic_client(ai_settings)
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    async with client.messages.stream(**kwargs) as stream:
        async for text in stream.text_stream:
            yield text


async def stream_chat(
    messages: list[dict],
    system: str = "",
    model: str | None = None,
    max_tokens: int = 8192,
) -> AsyncIterator[str]:
    """Streaming multi-turn chat. messages = [{"role": "user"|"assistant", "content": "..."}, ...]
    Preserves the full conversation history so Claude can reference previous turns."""
    ai_settings = _load_ai_settings()
    model = model or ai_settings["main_model"]

    if _is_openai(model):
        client = _get_openai_client(ai_settings)
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)
        stream = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=full_messages,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
        return

    client = _get_anthropic_client(ai_settings)
    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,  # full history, alternating user/assistant
    }
    if system:
        kwargs["system"] = system
    async with client.messages.stream(**kwargs) as stream:
        async for text in stream.text_stream:
            yield text


def sse_event(event: str, data: dict) -> str:
    """Format a Server-Sent Event string."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
