"""Tests for services/source_tier.py — publisher provenance classification.

The classification rides on the per-source summarization response, so the
parsing has one job that matters more than getting the tier right: never lose
the summary. A source with no badge costs a label; a source with no summary is
gone from the research entirely.

Run from the backend directory:
    ./venv/bin/python -m tests.test_source_tier
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from services.source_tier import (
    DEFAULT_TIER,
    TIERS,
    deterministic_tier,
    split_tier,
    summarize_tiers,
    tier_label,
)


# ── Deterministic floor: who published it is a fact about the domain ─────────

def test_government_and_intergovernmental_domains_are_official():
    for url in [
        "https://www.nist.gov/itl/ai-risk-management-framework",
        "https://digital-strategy.ec.europa.eu/en/policies/ai-act",
        "https://edpb.europa.eu/news/news_en",
        "https://www.gov.uk/government/publications",
        "https://www.who.int/publications/x",
        "https://www.oecd.org/digital/artificial-intelligence/",
        "https://www.mod.mil/report",
    ]:
        assert deterministic_tier(url) == "official", url


def test_lookalike_domains_are_not_official():
    """Suffix matching must anchor on a label boundary — "notgov.com" and
    "fakeeuropa.eu.example.com" must not inherit official status."""
    for url in [
        "https://notgov.com/page",
        "https://mygov.example.com/page",
        "https://europa.eu.phishing.example/page",
        "https://vendor.com/blog/gdpr-costs",
        "https://noyb.eu/en/some-campaign",
    ]:
        assert deterministic_tier(url) is None, url


def test_encyclopedia_and_self_publishing_platforms_are_general_web():
    """Observed misclassification this pins: en.wikipedia.org and a personal
    medium.com post both came back "journalism", which inflates the apparent
    quality of an evidence base. The publisher of a wiki page or a personal
    post is structurally general-web however scholarly the page reads."""
    for url in [
        "https://en.wikipedia.org/wiki/General_Data_Protection_Regulation",
        "https://medium.com/@byjoe/gdpr-compliance-cost-breakdown-for-startups-e04a158a9436",
        "https://someone.substack.com/p/a-post",
        "https://example.wordpress.com/2026/01/01/post",
        "https://www.reddit.com/r/gdpr/comments/x",
    ]:
        assert deterministic_tier(url) == "general_web", url


def test_general_web_rule_overrides_a_higher_model_classification():
    summary, tier = split_tier(
        "SOURCE_TYPE: journalism\nBody text.",
        "https://en.wikipedia.org/wiki/X",
    )
    assert tier == "general_web", tier
    assert summary == "Body text."


def test_official_domains_outrank_nothing_else_by_accident():
    """A general-web platform must not be reachable by the official rule, and
    vice versa — the two deterministic lists must stay disjoint."""
    assert deterministic_tier("https://en.wikipedia.org/wiki/X") == "general_web"
    assert deterministic_tier("https://www.nist.gov/x") == "official"


def test_malformed_url_does_not_raise():
    for url in ["", "not a url", "http://", "///"]:
        assert deterministic_tier(url) is None, url


def test_domain_rule_overrides_a_lower_model_classification():
    """The model reads the page; the domain says who served it. For official
    bodies the domain wins, so a regulator page cannot be labelled vendor."""
    summary, tier = split_tier("SOURCE_TYPE: vendor\nBody text.", "https://www.nist.gov/x")
    assert tier == "official", tier
    assert summary == "Body text."


# ── Parsing: the summary must survive anything ───────────────────────────────

def test_well_formed_response_splits_cleanly():
    summary, tier = split_tier(
        "SOURCE_TYPE: advocacy\nThe organisation reports that 1.3% of cases result in a fine.",
        "https://noyb.eu/en/x",
    )
    assert tier == "advocacy"
    assert summary == "The organisation reports that 1.3% of cases result in a fine."


def test_missing_classification_line_keeps_whole_text_as_summary():
    body = "A summary with no classification line at all."
    summary, tier = split_tier(body, "https://example.com/x")
    assert summary == body
    assert tier == DEFAULT_TIER


def test_unrecognised_tier_value_degrades_to_unknown_and_line_is_consumed():
    """A bogus value must not become a tier, and must not be left sitting at
    the top of the summary where it would flow into the synthesis."""
    summary, tier = split_tier("SOURCE_TYPE: banana\nReal body.", "https://example.com/x")
    assert tier == DEFAULT_TIER
    assert summary == "Real body."


def test_decorated_classification_line_still_parses():
    """Small-model output drifts into markdown; accept the common shapes."""
    for first in [
        "**SOURCE_TYPE: vendor**",
        "source_type: vendor",
        "SOURCE_TYPE:  Vendor ",
        "SOURCE_TYPE: `vendor`",
    ]:
        summary, tier = split_tier(f"{first}\nBody.", "https://example.com/x")
        assert tier == "vendor", first
        assert summary == "Body.", first


def test_hyphenated_and_spaced_tier_values_normalize():
    for value in ["peer-reviewed", "peer reviewed", "PEER_REVIEWED"]:
        _, tier = split_tier(f"SOURCE_TYPE: {value}\nBody.", "https://example.com/x")
        assert tier == "peer_reviewed", value


def test_classification_line_with_no_body_keeps_something():
    """Degenerate output must not produce an empty summary — losing the summary
    is strictly worse than keeping an odd one."""
    summary, tier = split_tier("SOURCE_TYPE: vendor", "https://example.com/x")
    assert summary, summary
    assert tier == "vendor"


def test_empty_response_is_handled():
    summary, tier = split_tier("", "https://example.com/x")
    assert summary == ""
    assert tier == DEFAULT_TIER


def test_multiline_body_is_preserved_intact():
    raw = "SOURCE_TYPE: journalism\nFirst line.\n\n- bullet one\n- bullet two\n\nClosing line."
    summary, tier = split_tier(raw, "https://example.com/x")
    assert tier == "journalism"
    assert summary == "First line.\n\n- bullet one\n- bullet two\n\nClosing line."


# ── Labels and aggregation ───────────────────────────────────────────────────

def test_every_tier_has_a_label():
    for key in TIERS:
        assert tier_label(key), key


def test_unknown_and_missing_tiers_render_as_unclassified():
    assert tier_label(None) == TIERS[DEFAULT_TIER]
    assert tier_label("not-a-tier") == TIERS[DEFAULT_TIER]


def test_breakdown_counts_in_tier_order_and_omits_empties():
    sources = [
        {"tier": "vendor"}, {"tier": "official"}, {"tier": "vendor"},
        {"tier": "advocacy"},
    ]
    assert summarize_tiers(sources) == [("official", 1), ("advocacy", 1), ("vendor", 2)]


def test_breakdown_treats_missing_and_bogus_tiers_as_unknown():
    assert summarize_tiers([{}, {"tier": None}, {"tier": "nonsense"}]) == [("unknown", 3)]


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
    print("\nRunning source-tier tests...\n")

    _run_test("gov/intergov domains are official", test_government_and_intergovernmental_domains_are_official)
    _run_test("lookalike domains are not official", test_lookalike_domains_are_not_official)
    _run_test("wiki/self-published are general_web", test_encyclopedia_and_self_publishing_platforms_are_general_web)
    _run_test("general_web rule overrides model", test_general_web_rule_overrides_a_higher_model_classification)
    _run_test("deterministic lists stay disjoint", test_official_domains_outrank_nothing_else_by_accident)
    _run_test("malformed url does not raise", test_malformed_url_does_not_raise)
    _run_test("domain rule overrides lower classification", test_domain_rule_overrides_a_lower_model_classification)
    _run_test("well-formed response splits cleanly", test_well_formed_response_splits_cleanly)
    _run_test("missing line keeps whole text", test_missing_classification_line_keeps_whole_text_as_summary)
    _run_test("unrecognised value degrades to unknown", test_unrecognised_tier_value_degrades_to_unknown_and_line_is_consumed)
    _run_test("decorated classification line parses", test_decorated_classification_line_still_parses)
    _run_test("hyphenated/spaced values normalize", test_hyphenated_and_spaced_tier_values_normalize)
    _run_test("classification with no body keeps something", test_classification_line_with_no_body_keeps_something)
    _run_test("empty response handled", test_empty_response_is_handled)
    _run_test("multiline body preserved", test_multiline_body_is_preserved_intact)
    _run_test("every tier has a label", test_every_tier_has_a_label)
    _run_test("unknown renders as unclassified", test_unknown_and_missing_tiers_render_as_unclassified)
    _run_test("breakdown counts in tier order", test_breakdown_counts_in_tier_order_and_omits_empties)
    _run_test("breakdown treats bogus as unknown", test_breakdown_treats_missing_and_bogus_tiers_as_unknown)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print("Failed: " + ", ".join(_FAILED))
        sys.exit(1)
    print("All tests passed.")
