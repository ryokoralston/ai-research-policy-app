"""Tests for services.consensus_meter.extract_consensus.

generate_json is monkeypatched — no API calls. Confirms the prompt includes
the formatted transcript and synthesis content inside <debate_transcript>/
<synthesis> tags, confirms the exact persona_keys given are listed (by name)
in the prompt, confirms the 8000-char truncation behavior on an oversized
transcript, and confirms the parsed dict is returned as-is from a mocked
JSON response.

Run from the backend directory:
    ./venv/bin/python -m tests.test_consensus_meter
"""
import asyncio
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

import services.consensus_meter as consensus_meter


def _patch_generate_json(fake):
    original = consensus_meter.generate_json
    consensus_meter.generate_json = fake
    return original


_HISTORY = [
    {"persona_name": "Dr. Sarah Chen", "round_name": "Opening Positions",
     "content": "Alignment research must come first."},
    {"persona_name": "Marcus Webb", "round_name": "Opening Positions",
     "content": "Regulation will hand leadership to China."},
]
_SYNTHESIS = "The debate revealed a split between safety-first and innovation-first camps."
_FAKE_RESULT = {
    "claims": [
        {"claim": "Regulation should precede deployment",
         "stances": {"safety_researcher": "agree", "tech_ceo": "disagree"}},
    ]
}


def test_prompt_includes_transcript_synthesis_and_listed_personas():
    captured = {}

    async def fake_gj(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return dict(_FAKE_RESULT)

    original = _patch_generate_json(fake_gj)
    try:
        result = asyncio.run(
            consensus_meter.extract_consensus(_HISTORY, _SYNTHESIS, ["safety_researcher", "tech_ceo"])
        )
    finally:
        consensus_meter.generate_json = original

    prompt = captured["prompt"]
    assert "<debate_transcript>" in prompt and "</debate_transcript>" in prompt
    assert "<synthesis>" in prompt and "</synthesis>" in prompt
    assert "Alignment research must come first." in prompt
    assert "Regulation will hand leadership to China." in prompt
    assert _SYNTHESIS in prompt
    # Exact persona keys must be listed with their real names, not invented.
    assert "safety_researcher (Dr. Sarah Chen)" in prompt
    assert "tech_ceo (Marcus Webb)" in prompt
    # Only the two given persona keys should be listed — not all 10.
    assert "military" not in prompt
    assert "civil_rights" not in prompt
    assert captured["kwargs"].get("temperature") == 0.0
    assert result == _FAKE_RESULT


def test_prompt_explains_mixed_fallback_for_unaddressed_claims():
    captured = {}

    async def fake_gj(prompt, **kwargs):
        captured["prompt"] = prompt
        return dict(_FAKE_RESULT)

    original = _patch_generate_json(fake_gj)
    try:
        asyncio.run(consensus_meter.extract_consensus(_HISTORY, _SYNTHESIS, ["safety_researcher", "tech_ceo"]))
    finally:
        consensus_meter.generate_json = original

    prompt = captured["prompt"]
    assert "mixed" in prompt.lower()
    assert "never addressed" in prompt.lower() or "not addressed" in prompt.lower()


def test_truncates_oversized_transcript():
    captured = {}

    async def fake_gj(prompt, **kwargs):
        captured["prompt"] = prompt
        return dict(_FAKE_RESULT)

    huge_history = [{"persona_name": "Dr. Sarah Chen", "round_name": "Opening Positions", "content": "X" * 20000}]

    original = _patch_generate_json(fake_gj)
    try:
        asyncio.run(consensus_meter.extract_consensus(huge_history, _SYNTHESIS, ["safety_researcher"]))
    finally:
        consensus_meter.generate_json = original

    prompt = captured["prompt"]
    assert "X" * 20000 not in prompt
    assert "X" * 100 in prompt
    assert len(prompt) < 20000 + 2000, "prompt should not balloon to the full untruncated transcript size"


def test_returns_parsed_dict_as_is():
    async def fake_gj(prompt, **kwargs):
        return {
            "claims": [
                {"claim": "AI licensing regime needed", "stances": {"regulator": "agree", "accelerationist": "disagree"}},
                {"claim": "China competition justifies speed", "stances": {"regulator": "mixed", "accelerationist": "agree"}},
            ]
        }

    original = _patch_generate_json(fake_gj)
    try:
        result = asyncio.run(
            consensus_meter.extract_consensus(_HISTORY, _SYNTHESIS, ["regulator", "accelerationist"])
        )
    finally:
        consensus_meter.generate_json = original

    assert len(result["claims"]) == 2
    assert result["claims"][0]["stances"]["regulator"] == "agree"
    assert result["claims"][1]["stances"]["accelerationist"] == "agree"


def test_exceptions_propagate():
    async def broken_gj(prompt, **kwargs):
        raise RuntimeError("api down")

    original = _patch_generate_json(broken_gj)
    try:
        try:
            asyncio.run(consensus_meter.extract_consensus(_HISTORY, _SYNTHESIS, ["safety_researcher"]))
            raise AssertionError("expected RuntimeError to propagate")
        except RuntimeError as exc:
            assert "api down" in str(exc)
    finally:
        consensus_meter.generate_json = original


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
    print("\nRunning consensus_meter tests...\n")

    _run("prompt includes transcript, synthesis, and listed personas", test_prompt_includes_transcript_synthesis_and_listed_personas)
    _run("prompt explains mixed fallback for unaddressed claims", test_prompt_explains_mixed_fallback_for_unaddressed_claims)
    _run("truncates oversized transcript", test_truncates_oversized_transcript)
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
