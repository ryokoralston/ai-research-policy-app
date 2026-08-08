"""Tests for per-subject-type risk dimension sets.

A regulation scored on "Technical Capability Level" produces a number that
points the opposite way from its own analysis — GDPR scored 2/10 there while
the text argued its oversight machinery is dangerously weak. These pin the
mapping that prevents that, the invariants every set must hold, and the
fallback that keeps the free-form analysis_type contract intact.

Run from the backend directory:
    ./venv/bin/python -m tests.test_dimension_sets
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from templates import DIMENSION_REGISTRY, DIMENSION_SETS, RISK_DIMENSIONS, dimensions_for
from templates.risk_assessment import RISK_ASSESSMENT_SECTIONS


# ── Selection ────────────────────────────────────────────────────────────────

def test_each_frontend_analysis_type_has_a_set():
    """Every id offered by the frontend's ANALYSIS_TYPES must map explicitly,
    so a listed subject type never silently lands on the fallback."""
    offered = {"technology", "policy", "actor", "use_case", "supply_chain"}
    assert offered <= set(DIMENSION_SETS), offered - set(DIMENSION_SETS)


def test_policy_is_not_scored_on_technology_axes():
    """The whole point: a regulation is not a system, so it is not scored on
    a system's descriptive properties."""
    keys = {d["key"] for d in dimensions_for("policy")}
    assert "capability" not in keys, keys
    assert "deployment" not in keys, keys
    # ...and it IS scored on the axes a regulation actually has.
    assert {"enforcement", "uncertainty", "fragmentation", "burden"} <= keys, keys


def test_actor_is_not_scored_on_technology_axes():
    keys = {d["key"] for d in dimensions_for("actor")}
    assert "capability" not in keys, keys
    assert "deployment" not in keys, keys
    assert {"governance_maturity", "accountability", "concentration"} <= keys, keys


def test_technology_set_is_unchanged():
    """Existing technology assessments must keep scoring exactly the same
    dimensions in the same order — a reordering would silently change how
    older analyses compare to newer ones."""
    assert [d["key"] for d in dimensions_for("technology")] == [
        "capability", "deployment", "governance", "geopolitical",
        "misuse", "equity", "systemic",
    ]


def test_unknown_type_falls_back_to_default_set():
    """analysis_type is a free-form string injected verbatim into the prompt
    (schemas/analysis.py), so it is NOT constrained to DIMENSION_SETS' keys.
    Anything unrecognised must still produce a full assessment."""
    for unknown in ["", "vendor", "AI model", "made-up-type"]:
        assert dimensions_for(unknown) is RISK_DIMENSIONS, unknown


def test_equity_shared_by_every_set_is_the_same_object():
    """Shared dimensions are composed from one definition, not copied — a
    fix to the equity criteria must reach every set at once."""
    for name, dims in DIMENSION_SETS.items():
        equity = next(d for d in dims if d["key"] == "equity")
        assert equity is DIMENSION_REGISTRY["equity"], name


# ── Invariants every set must hold ───────────────────────────────────────────

def test_every_set_has_the_same_dimension_count():
    """run_risk_analysis makes one parallel LLM call per dimension, so an
    uneven set would make cost and latency depend on subject type."""
    counts = {name: len(dims) for name, dims in DIMENSION_SETS.items()}
    assert len(set(counts.values())) == 1, counts


def test_no_set_has_duplicate_keys():
    for name, dims in DIMENSION_SETS.items():
        keys = [d["key"] for d in dims]
        assert len(keys) == len(set(keys)), (name, keys)


def test_every_dimension_is_fully_specified():
    for key, dim in DIMENSION_REGISTRY.items():
        assert dim["key"] == key, (key, dim["key"])
        assert dim["title"].strip(), key
        assert dim["scale"].startswith("(1=") and "10=" in dim["scale"], (key, dim["scale"])
        assert len(dim["criteria"]) >= 3, (key, len(dim["criteria"]))
        assert all(c.strip() for c in dim["criteria"]), key


def test_every_registry_dimension_is_used_by_some_set():
    """A definition no set references is dead weight that still looks live."""
    used = {d["key"] for dims in DIMENSION_SETS.values() for d in dims}
    assert set(DIMENSION_REGISTRY) == used, set(DIMENSION_REGISTRY) - used


def test_titles_are_unique_across_the_registry():
    """_build_dimension_prompt isolates a call to one dimension by title, and
    the tests identify a prompt's dimension the same way — two dimensions
    sharing a title would make both ambiguous."""
    titles = [d["title"] for d in DIMENSION_REGISTRY.values()]
    assert len(titles) == len(set(titles)), titles


def test_no_title_is_a_substring_of_another():
    """Same reason, one step stricter: title matching is substring-based, so
    "Governance Gap" inside "Internal Governance Gap" would cross-match."""
    titles = [d["title"] for d in DIMENSION_REGISTRY.values()]
    for a in titles:
        for b in titles:
            if a != b:
                assert a not in b, (a, b)


def test_documented_default_matches_the_system_set():
    """RISK_ASSESSMENT_SECTIONS' risk_dimensions instructions are superseded by
    the parallel path but kept as documentation; they must still describe the
    default set or they mislead the next reader."""
    instructions = next(
        s for s in RISK_ASSESSMENT_SECTIONS if s["key"] == "risk_dimensions"
    )["instructions"]
    for dim in RISK_DIMENSIONS:
        assert dim["title"] in instructions, dim["title"]


# ── Test runner ───────────────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run_test(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")


if __name__ == "__main__":
    print("\nRunning dimension-set tests...\n")

    _run_test("every frontend analysis type has a set", test_each_frontend_analysis_type_has_a_set)
    _run_test("policy not scored on technology axes", test_policy_is_not_scored_on_technology_axes)
    _run_test("actor not scored on technology axes", test_actor_is_not_scored_on_technology_axes)
    _run_test("technology set unchanged", test_technology_set_is_unchanged)
    _run_test("unknown type falls back to default", test_unknown_type_falls_back_to_default_set)
    _run_test("shared equity is one object", test_equity_shared_by_every_set_is_the_same_object)
    _run_test("all sets same dimension count", test_every_set_has_the_same_dimension_count)
    _run_test("no duplicate keys in a set", test_no_set_has_duplicate_keys)
    _run_test("every dimension fully specified", test_every_dimension_is_fully_specified)
    _run_test("no unused registry dimension", test_every_registry_dimension_is_used_by_some_set)
    _run_test("titles unique across registry", test_titles_are_unique_across_the_registry)
    _run_test("no title is a substring of another", test_no_title_is_a_substring_of_another)
    _run_test("documented default matches system set", test_documented_default_matches_the_system_set)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print("Failed: " + ", ".join(_FAILED))
        sys.exit(1)
    print("All tests passed.")
