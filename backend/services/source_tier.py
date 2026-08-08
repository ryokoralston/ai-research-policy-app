"""Provenance tiering for research sources.

A finished assessment cites official regulator guidance, peer-reviewed work,
advocacy campaigns and vendor marketing with identical `[Source N]` markers.
The model already notices the difference in prose — GDPR's assessment flagged
one figure as "advocacy-sourced and untested" and another as having
"methodology undisclosed" — but nothing structured recorded it, so a reader
scanning the source list could not see that a cost claim came from a vendor
blog while a fine-rate claim came from a campaigning organisation.

DESIGN RULE: a tier is provenance, not truth. A vendor blog can be accurate
and a peer-reviewed paper can be wrong. Tiers are therefore used only for
display and as context in the synthesis prompt — never to weight, discount or
score a claim arithmetically. Numeric confidence derived from a provenance
label would look auditable while adding no evidence, which is the same trap as
multiplying LLM-estimated ordinals together.

Classification rides on the per-source summarization call that already reads
each page (research_agent._summarize_source), so tiering costs no extra API
calls. `official` additionally has a deterministic floor: a page served from a
government or intergovernmental domain is published by an official body as a
matter of fact, not of judgement, so the domain rule decides it and the model
cannot talk it down.
"""
from urllib.parse import urlparse

# Ordered most to least authoritative. `unknown` is deliberately last and
# deliberately NOT the lowest tier in meaning: it records "not classified",
# which is not the same as "untrustworthy". Same rule as an absent grounding
# score in the risk dimensions.
TIERS: dict[str, str] = {
    "official": "Official / Regulator",
    "peer_reviewed": "Peer-reviewed",
    "journalism": "Research & Journalism",
    "advocacy": "Advocacy",
    "vendor": "Vendor / Commercial",
    "general_web": "General web / Self-published",
    "unknown": "Unclassified",
}

DEFAULT_TIER = "unknown"

# The line the summarization prompt asks for, and the marker we split on.
TIER_FIELD = "SOURCE_TYPE"

# Only the tiers the model is asked to choose between. `official` is omitted
# from the instruction wording deliberately — see _OFFICIAL_* below — but is
# still accepted if returned, because official bodies also publish on domains
# no suffix rule can enumerate (oecd.org, un.org, standards bodies).
TIER_INSTRUCTION = (
    f"First output a single line of the form `{TIER_FIELD}: <type>` classifying "
    f"the PUBLISHER of this source, choosing exactly one of: "
    f"official (a government, regulator or intergovernmental body), "
    f"peer_reviewed (an academic journal or peer-reviewed venue), "
    f"journalism (a news organisation, research institute or think tank), "
    f"advocacy (an organisation campaigning for a stated position), "
    f"vendor (a company writing about its own market or product), "
    f"general_web (an encyclopedia, wiki, content platform, or a personal or "
    f"self-published post — anything with no editorial or review process). "
    f"Judge the publisher, not whether you agree with the content. "
    f"Then output the summary, starting on the next line."
)

# Deterministic floor for `official`. Suffix match on the registrable host, so
# "edpb.europa.eu" matches "europa.eu" and "notgov.example.com" does not match
# ".gov".
_OFFICIAL_SUFFIXES = (
    ".gov", ".mil", ".int", ".gov.uk", ".go.jp", ".gouv.fr", ".gc.ca", ".govt.nz",
)
_OFFICIAL_DOMAINS = (
    "europa.eu", "oecd.org", "un.org", "who.int", "coe.int", "bis.org", "imf.org",
    "worldbank.org", "nist.gov", "iso.org", "itu.int",
)


# Platforms whose publisher is structurally general-web regardless of how
# scholarly an individual page reads. Observed misclassification this fixes:
# en.wikipedia.org and a personal medium.com post were both returned as
# "journalism", which inflates the apparent quality of an evidence base.
_GENERAL_WEB_DOMAINS = (
    "wikipedia.org", "wikimedia.org", "medium.com", "substack.com",
    "blogspot.com", "wordpress.com", "reddit.com", "quora.com",
    "linkedin.com", "notion.site", "github.io",
)


def deterministic_tier(url: str) -> str | None:
    """The tier fixed by the domain, or None to leave it to the model.

    Kept separate from the model's judgement because these are facts rather
    than readings: a document served from europa.eu is published by an EU body,
    and a wikipedia.org page is an encyclopedia entry, regardless of what any
    classifier makes of the contents.
    """
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not host:
        return None
    if host.endswith(_OFFICIAL_SUFFIXES):
        return "official"
    for domain in _OFFICIAL_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return "official"
    for domain in _GENERAL_WEB_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return "general_web"
    return None


def split_tier(raw: str, url: str = "") -> tuple[str, str]:
    """Split a summarization response into (summary, tier).

    Degrades rather than fails: an unparseable or missing SOURCE_TYPE line
    returns the whole response as the summary with tier `unknown`. Losing the
    tier costs a badge; losing the summary would cost the source itself, and
    the summary is what the assessment is actually written from.
    """
    text = (raw or "").strip()
    summary, tier = text, DEFAULT_TIER

    first, newline, rest = text.partition("\n")
    label = first.strip().lstrip("*# ").rstrip("*")
    if label.upper().startswith(TIER_FIELD + ":"):
        candidate = label.split(":", 1)[1].strip().strip("`*_").lower()
        candidate = candidate.replace("-", "_").replace(" ", "_")
        if candidate in TIERS:
            tier = candidate
        # The line is consumed either way: leaving a malformed classification
        # line at the top of the summary would feed it into the synthesis.
        summary = rest.strip() if newline else ""

    # The domain rule wins where it applies.
    fixed = deterministic_tier(url)
    if fixed:
        tier = fixed

    return summary or text, tier


def tier_label(tier: str | None) -> str:
    return TIERS.get(tier or DEFAULT_TIER, TIERS[DEFAULT_TIER])


def summarize_tiers(sources: list[dict]) -> list[tuple[str, int]]:
    """Counts per tier in TIERS order, omitting tiers with none.

    Feeds the one-line provenance breakdown on the source list — the
    report-level read of "what is this assessment actually built on".
    """
    counts: dict[str, int] = {}
    for s in sources:
        tier = s.get("tier") or DEFAULT_TIER
        if tier not in TIERS:
            tier = DEFAULT_TIER
        counts[tier] = counts.get(tier, 0) + 1
    return [(t, counts[t]) for t in TIERS if t in counts]
