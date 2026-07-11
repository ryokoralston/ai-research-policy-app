"""Tests for image-document ingestion: the pure helpers in
services/anthropic_client.py (image_media_type, _image_message_content,
generate_text_with_image's OpenAI guard) plus a sanity check on
rag_service.IMAGE_DESCRIPTION_PROMPT.

No live API calls — generate_text_with_image's OpenAI-rejection path raises
before any client is constructed, and _load_ai_settings is patched to avoid
a real DB round-trip.

Run from the backend directory:
    ./venv/bin/python -m tests.test_image_ingestion
"""
import asyncio
import base64
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

import services.anthropic_client as anthropic_client


# ── image_media_type ──────────────────────────────────────────────────────────

def test_image_media_type_supported_extensions():
    cases = {
        "photo.png": "image/png",
        "photo.PNG": "image/png",
        "photo.jpg": "image/jpeg",
        "photo.JPG": "image/jpeg",
        "photo.jpeg": "image/jpeg",
        "photo.JPEG": "image/jpeg",
        "photo.webp": "image/webp",
        "photo.WEBP": "image/webp",
        "photo.gif": "image/gif",
        "photo.GIF": "image/gif",
        "/some/dir/scan.Png": "image/png",
    }
    for filename, expected in cases.items():
        assert anthropic_client.image_media_type(filename) == expected, (filename, anthropic_client.image_media_type(filename))


def test_image_media_type_unsupported_returns_none():
    assert anthropic_client.image_media_type("report.pdf") is None
    assert anthropic_client.image_media_type("notes.txt") is None
    assert anthropic_client.image_media_type("no_extension") is None
    assert anthropic_client.image_media_type("") is None


# ── IMAGE_DESCRIPTION_PROMPT sanity ───────────────────────────────────────────

def test_description_prompt_mentions_transcription():
    from services.rag_service import IMAGE_DESCRIPTION_PROMPT
    lowered = IMAGE_DESCRIPTION_PROMPT.lower()
    assert "transcribe" in lowered, IMAGE_DESCRIPTION_PROMPT
    assert "chart" in lowered, IMAGE_DESCRIPTION_PROMPT


def test_image_doc_marker_exists():
    from services.rag_service import IMAGE_DOC_MARKER
    assert IMAGE_DOC_MARKER.strip(), "marker must be non-empty"
    assert "image" in IMAGE_DOC_MARKER.lower()


# ── _image_message_content ────────────────────────────────────────────────────

def test_image_message_content_shape_and_roundtrip():
    image_bytes = b"\x89PNG\r\n\x1a\nthis-is-fake-png-bytes-for-testing-purposes"
    blocks = anthropic_client._image_message_content(image_bytes, "image/png", "Describe this image")

    assert len(blocks) == 2, blocks
    image_block, text_block = blocks

    assert image_block["type"] == "image", image_block
    assert image_block["source"]["type"] == "base64", image_block
    assert image_block["source"]["media_type"] == "image/png", image_block
    # base64 round-trips to the exact original bytes
    decoded = base64.standard_b64decode(image_block["source"]["data"])
    assert decoded == image_bytes, decoded
    # no embedded newlines (standard_b64encode never inserts them, but pin
    # the behavior explicitly since the API requires a clean base64 string)
    assert "\n" not in image_block["source"]["data"]

    assert text_block == {"type": "text", "text": "Describe this image"}, text_block


def test_image_message_content_different_media_type():
    blocks = anthropic_client._image_message_content(b"fake-jpeg-bytes", "image/jpeg", "prompt text")
    assert blocks[0]["source"]["media_type"] == "image/jpeg"
    assert blocks[1]["text"] == "prompt text"


# ── generate_text_with_image: OpenAI model rejection ──────────────────────────

def test_generate_text_with_image_rejects_openai_model():
    original_load = anthropic_client._load_ai_settings
    anthropic_client._load_ai_settings = lambda: {
        "main_model": "claude-opus-4-6",
        "fast_model": "claude-haiku-4-5-20251001",
        "anthropic_api_key": "",
        "openai_api_key": "",
    }
    try:
        raised = False
        try:
            asyncio.run(anthropic_client.generate_text_with_image(
                "describe this image", b"fake-bytes", "image/png", model="gpt-4o",
            ))
        except ValueError:
            raised = True
        assert raised, "expected ValueError for an OpenAI model"
    finally:
        anthropic_client._load_ai_settings = original_load


def test_generate_text_with_image_rejects_openai_default_main_model():
    """Same guard, but relying on the default (ai_settings['main_model']) being
    an OpenAI model rather than an explicit model= argument."""
    original_load = anthropic_client._load_ai_settings
    anthropic_client._load_ai_settings = lambda: {
        "main_model": "gpt-4o",
        "fast_model": "gpt-4o-mini",
        "anthropic_api_key": "",
        "openai_api_key": "",
    }
    try:
        raised = False
        try:
            asyncio.run(anthropic_client.generate_text_with_image(
                "describe this image", b"fake-bytes", "image/png",
            ))
        except ValueError:
            raised = True
        assert raised, "expected ValueError when the default main_model is an OpenAI model"
    finally:
        anthropic_client._load_ai_settings = original_load


# ── Test runner ────────────────────────────────────────────────────────────────

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
    print("\nRunning image ingestion tests...\n")

    _run("image_media_type: supported extensions", test_image_media_type_supported_extensions)
    _run("image_media_type: unsupported returns None", test_image_media_type_unsupported_returns_none)
    _run("IMAGE_DESCRIPTION_PROMPT mentions transcription/chart", test_description_prompt_mentions_transcription)
    _run("IMAGE_DOC_MARKER exists and mentions image", test_image_doc_marker_exists)
    _run("_image_message_content shape + base64 round-trip", test_image_message_content_shape_and_roundtrip)
    _run("_image_message_content different media_type", test_image_message_content_different_media_type)
    _run("generate_text_with_image rejects explicit OpenAI model", test_generate_text_with_image_rejects_openai_model)
    _run("generate_text_with_image rejects OpenAI default main_model", test_generate_text_with_image_rejects_openai_default_main_model)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
