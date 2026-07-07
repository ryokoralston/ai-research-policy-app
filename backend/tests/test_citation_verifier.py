"""Tests for services.citation_verifier.verify_grounding.

generate_json is monkeypatched — no API calls. Confirms the prompt includes
both <source_material> and <generated_content> tags with the actual content,
confirms truncation behavior on oversized inputs, and confirms the parsed
dict is returned as-is from a mocked JSON response.

Run from the backend directory:
    ./venv/bin/python -m tests.test_citation_verifier
"""
import asyncio
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

import services.citation_verifier as citation_verifier


def _patch_generate_json(fake):
    original = citation_verifier.generate_json
    citation_verifier.generate_json = fake
    return original


def test_prompt_includes_both_xml_tags_and_content():
    captured = {}

    async def fake_gj(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {"confidence_score": 9, "unsupported_claims": [], "notes": "well grounded"}

    original = _patch_generate_json(fake_gj)
    try:
        result = asyncio.run(
            citation_verifier.verify_grounding(
                "The sky is blue according to the source.",
                "Source: the sky is blue.",
            )
        )
    finally:
        citation_verifier.generate_json = original

    prompt = captured["prompt"]
    assert "<source_material>" in prompt and "</source_material>" in prompt
    assert "<generated_content>" in prompt and "</generated_content>" in prompt
    assert "Source: the sky is blue." in prompt
    assert "The sky is blue according to the source." in prompt
    assert captured["kwargs"].get("temperature") == 0.0
    assert result == {"confidence_score": 9, "unsupported_claims": [], "notes": "well grounded"}


def test_truncates_oversized_inputs():
    captured = {}

    async def fake_gj(prompt, **kwargs):
        captured["prompt"] = prompt
        return {"confidence_score": 5, "unsupported_claims": [], "notes": "n/a"}

    huge_content = "C" * 20000
    huge_source = "S" * 20000

    original = _patch_generate_json(fake_gj)
    try:
        asyncio.run(citation_verifier.verify_grounding(huge_content, huge_source))
    finally:
        citation_verifier.generate_json = original

    prompt = captured["prompt"]
    # Full untruncated strings must NOT appear in the prompt
    assert huge_content not in prompt
    assert huge_source not in prompt
    # But a truncated prefix must be present
    assert "C" * 100 in prompt
    assert "S" * 100 in prompt
    # Sanity: prompt shouldn't balloon to the full 40000+ chars of raw input
    assert len(prompt) < len(huge_content) + len(huge_source)


def test_returns_parsed_dict_as_is():
    async def fake_gj(prompt, **kwargs):
        return {
            "confidence_score": 3,
            "unsupported_claims": ["claim A not in source", "claim B fabricated"],
            "notes": "several unsupported claims",
        }

    original = _patch_generate_json(fake_gj)
    try:
        result = asyncio.run(citation_verifier.verify_grounding("content", "source"))
    finally:
        citation_verifier.generate_json = original

    assert result["confidence_score"] == 3
    assert result["unsupported_claims"] == ["claim A not in source", "claim B fabricated"]
    assert result["notes"] == "several unsupported claims"


def test_exceptions_propagate():
    async def broken_gj(prompt, **kwargs):
        raise RuntimeError("api down")

    original = _patch_generate_json(broken_gj)
    try:
        try:
            asyncio.run(citation_verifier.verify_grounding("content", "source"))
            raise AssertionError("expected RuntimeError to propagate")
        except RuntimeError as exc:
            assert "api down" in str(exc)
    finally:
        citation_verifier.generate_json = original


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
    print("\nRunning citation_verifier tests...\n")

    _run("prompt includes both XML tags and content", test_prompt_includes_both_xml_tags_and_content)
    _run("truncates oversized inputs", test_truncates_oversized_inputs)
    _run("returns parsed dict as-is", test_returns_parsed_dict_as_is)
    _run("exceptions propagate", test_exceptions_propagate)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
