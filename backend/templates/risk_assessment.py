RISK_ASSESSMENT_SYSTEM = """You are a senior AI risk analyst at a policy research institute conducting a structured risk assessment. Your assessments follow a rigorous analytical framework and are used by policymakers to understand emerging technology risks.

Requirements:
- Score each risk dimension 1-10 with clear justification
- Distinguish clearly between near-term (1-2 year), medium-term (3-5 year), and long-term (5+ year) risks
- Ground all claims in available evidence
- Be calibrated: avoid both excessive alarm and unwarranted reassurance
- Format in Markdown with clear headers"""

RISK_ASSESSMENT_SECTIONS = [
    {
        "key": "subject_profile",
        "title": "Subject Profile",
        "instructions": (
            "Describe the subject being assessed in 150-200 words: "
            "what it is, its current state of development/deployment, "
            "key actors involved (developers, deployers, users), "
            "and why it merits a risk assessment now."
        ),
    },
    {
        "key": "risk_dimensions",
        "title": "Risk Dimensions",
        # NOTE: these `instructions` are SUPERSEDED by the parallel per-dimension
        # path in services/risk_analyzer.py (run_risk_analysis + RISK_DIMENSIONS
        # below) — that code runs 6 independent stream_text_with_thinking calls,
        # one per dimension, instead of a single call driven by this string. This
        # entry's `key`/`title` are still the section's identity (section_start/
        # section_end SSE events, the "## Risk Dimensions" heading in the
        # assembled report), so the entry stays in this list; `instructions` is
        # dead for this key only and kept for reference/documentation.
        "instructions": (
            "For each of the risk dimensions below, provide:\n"
            "- Score: X/10 (with brief justification in parentheses)\n"
            "- 2-3 sentence analysis\n\n"
            "Dimensions to assess:\n"
            "1. **Technical Capability Level** (1=minimal, 10=transformative/superhuman)\n"
            "2. **Deployment/Proliferation Speed** (1=slow/limited, 10=rapid/widespread)\n"
            "3. **Governance & Oversight Gap** (1=well-governed, 10=governance vacuum)\n"
            "4. **Geopolitical Risk Concentration** (1=distributed/benign, 10=concentrated/adversarial)\n"
            "5. **Misuse Potential** (1=difficult to misuse, 10=trivially weaponizable)\n"
            "6. **Rights & Equity Impact** (1=negligible, 10=severe rights violations at scale)\n"
            "7. **Systemic/Cascading Risk** (1=contained, 10=civilization-scale)"
        ),
    },
    {
        "key": "scenarios",
        "title": "Key Risk Scenarios",
        "instructions": (
            "Describe 3 scenarios (200-300 words total):\n"
            "**Optimistic Scenario**: What does success look like? What conditions make this likely?\n"
            "**Baseline Scenario**: Most probable trajectory given current trends. Key uncertainties.\n"
            "**Pessimistic Scenario**: Credible worst case. What conditions would produce this?\n"
            "Assign rough probability ranges to each."
        ),
    },
    {
        "key": "existing_safeguards",
        "title": "Existing Safeguards & Gaps",
        "instructions": (
            "Identify (200-250 words):\n"
            "- What safeguards currently exist (technical, regulatory, international)\n"
            "- Which are effective vs. inadequate\n"
            "- The 2-3 most critical governance gaps\n"
            "- Any analogies to how similar risks were managed historically"
        ),
    },
    {
        "key": "mitigation_options",
        "title": "Risk Mitigation Options",
        "instructions": (
            "Present 3-4 risk mitigation options in order from most to least feasible "
            "(200-300 words total). For each: what it is, who implements it, "
            "effectiveness estimate, and key obstacles."
        ),
    },
    {
        "key": "monitoring_indicators",
        "title": "Monitoring Indicators",
        "instructions": (
            "List 4-6 specific, observable indicators that policymakers should monitor "
            "to track how this risk is evolving (150-200 words). "
            "For each indicator: what to watch, what threshold would warrant escalated response."
        ),
    },
]

# One entry per risk_dimensions sub-dimension, used by the parallel analysis
# path in services/risk_analyzer.py (_build_dimension_prompt / _analyze_dimension
# / run_risk_analysis) instead of the single crammed-together prompt that
# RISK_ASSESSMENT_SECTIONS' risk_dimensions["instructions"] describes.
#
# DESIGN RULE for every dimension below: it must answer one question — "how
# much does this raise the risk?" — on a scale where 10 is always worse. A
# dimension that describes a property instead of a risk produces a score that
# points the opposite way from its own analysis. That is exactly what happened
# when a regulation was assessed against the technology-shaped set: GDPR scored
# "Technical Capability Level 2/10", which reads as low risk, while the text
# under it argued the oversight machinery is dangerously weak.
#
# KNOWN EXCEPTION, deliberately kept: `capability` and `deployment` in the
# system set below are still descriptive axes rather than risk axes — the same
# category of problem, left alone because for an AI system they correlate with
# risk closely enough to be useful. Revisit if a technology assessment shows
# the same score/text divergence.
#
# `title` matches the bold dimension names and `scale` matches the 1=.../10=...
# anchors previously embedded in the single prompt above. `criteria` is 3-5
# bullet points of considerations unique to that dimension, so each parallel
# call gets a substantive, distinct rubric.
#
# Definitions live here once and are composed into per-subject-type sets at the
# bottom of this file (DIMENSION_SETS / dimensions_for) — a dimension shared by
# several sets is defined once, not copied.

# ── System set: things that are built and deployed ───────────────────────────
# Kept under the name RISK_DIMENSIONS because it is also the DEFAULT set for
# any unrecognised analysis_type, and because the test suites import this name
# and run with analysis_type="technology".
RISK_DIMENSIONS: list[dict] = [
    {
        "key": "capability",
        "title": "Technical Capability Level",
        "scale": "(1=minimal, 10=transformative/superhuman)",
        "criteria": [
            "Current performance on relevant benchmarks or real-world tasks relative to human experts",
            "Degree of autonomy and generality — a narrow single-purpose tool vs. a general-purpose agent",
            "Rate of capability improvement across recent development cycles",
            "Gap between current capability and the safeguards/oversight mechanisms built around it",
        ],
    },
    {
        "key": "deployment",
        "title": "Deployment/Proliferation Speed",
        "scale": "(1=slow/limited, 10=rapid/widespread)",
        "criteria": [
            "Number and diversity of current deployment contexts (research, commercial, consumer, critical infrastructure)",
            "Ease of access — open weights, API-gated, or restricted to a small number of actors",
            "Speed of the adoption curve and its geographic/sectoral spread",
            "Barriers that would slow further proliferation (cost, compute, specialized expertise)",
        ],
    },
    {
        "key": "governance",
        "title": "Governance & Oversight Gap",
        "scale": "(1=well-governed, 10=governance vacuum)",
        "criteria": [
            "Existence and enforceability of applicable regulation, standards, or licensing regimes",
            "Presence of independent oversight, auditing, or certification mechanisms",
            "International coordination vs. fragmented or conflicting jurisdictional approaches",
            "Whether governance capacity is keeping pace with the technology's development speed",
        ],
    },
    {
        "key": "geopolitical",
        "title": "Geopolitical Risk Concentration",
        "scale": "(1=distributed/benign, 10=concentrated/adversarial)",
        "criteria": [
            "Concentration of development and control among a small number of states or firms",
            "Presence of adversarial or strategic-competition dynamics around the technology",
            "Coverage and effectiveness of export controls or similar restrictions",
            "Potential for the technology to shift military or economic balance of power",
        ],
    },
    {
        "key": "misuse",
        "title": "Misuse Potential",
        "scale": "(1=difficult to misuse, 10=trivially weaponizable)",
        "criteria": [
            "Accessibility of the capability to non-state or malicious actors",
            "Dual-use surface — how readily beneficial uses convert into harmful ones",
            "Technical expertise and resources an attacker would need to misuse it",
            "Existing precedents or documented instances of misuse",
        ],
    },
    # Shared by every set. The framing is deliberately two-sided: a subject can
    # score high here either by violating rights or by promising protections it
    # fails to deliver. A rights-protective subject assessed on the one-sided
    # "harm caused" wording inverts — GDPR's own assessment had to talk its way
    # out of it, scoring the *gap* between nominal and effective rights while
    # the rubric asked about harm the instrument causes.
    {
        "key": "equity",
        "title": "Rights & Equity Impact",
        "scale": "(1=negligible, 10=severe rights violations, or protections that fail at scale)",
        "criteria": [
            "Harm to fundamental rights — privacy, due process, non-discrimination, freedom of expression — whether caused directly or left unprevented",
            "For a subject that promises protection: the gap between the rights it nominally confers and what people actually receive",
            "Evidence of disparate impact or unequal performance across demographic groups or jurisdictions",
            "Whether it bears on domains where decisions materially affect life chances (employment, credit, housing, healthcare, education, law enforcement, migration)",
            "Availability of recourse: can an affected person contest, appeal, or obtain an explanation — and does that recourse work equally for everyone",
            "Distribution of benefits vs. harms — whether the populations bearing the risk are the ones gaining the value",
        ],
    },
    {
        "key": "systemic",
        "title": "Systemic/Cascading Risk",
        "scale": "(1=contained, 10=civilization-scale)",
        "criteria": [
            "Potential for cascading failures across interconnected systems",
            "Degree of societal or economic dependence being built on top of it",
            "Reversibility of harms if something goes wrong",
            "Correlation with other risk vectors — whether a failure here compounds other systemic risks",
        ],
    },
]


# ── Instrument set: rules that are enacted and enforced ──────────────────────
# For a regulation, law, standard or framework, three of the system set's
# dimensions are category errors: `capability` and `deployment` describe a
# technology's properties, and `misuse` assumes the subject confers a
# capability rather than imposing an obligation. (GDPR's misuse score was the
# weakest section of that assessment — 4/10 at low confidence, with claims
# withdrawn for want of evidence.)
#
# `governance` is not dropped so much as DECOMPOSED. Reading GDPR's governance
# section, everything it actually argued fell into three questions: is there
# capacity to enforce, is the meaning settled, and is it applied consistently?
# Those are `enforcement`, `uncertainty` and `fragmentation` below.
_INSTRUMENT_ONLY: list[dict] = [
    {
        "key": "enforcement",
        "title": "Enforcement Capacity Gap",
        "scale": "(1=well-resourced and consistently enforced, 10=paper rule with no effective enforcement)",
        "criteria": [
            "Resourcing of the bodies charged with enforcement, measured against caseload rather than against national income",
            "Specialist expertise those bodies hold for the domain being regulated",
            "Observed enforcement outcomes — action rates, penalties actually imposed, time to final decision",
            "Whether enforcement capacity is growing, static, or declining",
        ],
    },
    {
        "key": "uncertainty",
        "title": "Legal & Interpretive Uncertainty",
        "scale": "(1=settled and predictable, 10=core obligations fundamentally unresolved)",
        "criteria": [
            "Whether central terms and obligations have settled, authoritative interpretation",
            "Presence of binding guidance versus case-by-case litigation as the resolution mechanism",
            "Unresolved interfaces or doctrinal conflicts with adjacent instruments",
            "How quickly interpretive gaps are actually closing, and by what mechanism",
        ],
    },
    {
        "key": "fragmentation",
        "title": "Fragmentation & Inconsistent Application",
        "scale": "(1=uniform application, 10=order-of-magnitude divergence across jurisdictions)",
        "criteria": [
            "Divergence in outcomes across the jurisdictions or authorities applying it",
            "Effectiveness of the coordination and consistency mechanisms that exist",
            "Opportunity for forum shopping toward the most permissive authority",
            "Whether procedural reform is narrowing or widening that divergence",
        ],
    },
    {
        "key": "burden",
        "title": "Compliance Burden & Distributional Effect",
        "scale": "(1=proportionate across regulated entities, 10=severely regressive)",
        "criteria": [
            "Absolute compliance cost, and the evidentiary quality of the estimates behind it",
            "How that cost falls across large incumbents versus smaller entities",
            "Whether the burden entrenches existing market positions",
            "Availability and adequacy of proportionality mechanisms or exemptions",
        ],
    },
]


# ── Organization set: actors that build, deploy or govern ────────────────────
# `capability` and `deployment` again describe a technology rather than an
# actor; what matters for an organization is whether anyone can hold it to
# account and how much of the ecosystem depends on it.
_ORGANIZATION_ONLY: list[dict] = [
    {
        "key": "governance_maturity",
        "title": "Internal Governance Maturity",
        "scale": "(1=mature and independently assured, 10=no meaningful internal governance)",
        "criteria": [
            "Existence of internal review, red-teaming or release-gating processes that hold real authority",
            "Whether stated commitments are externally verifiable or self-attested only",
            "Track record of honouring versus quietly abandoning prior public commitments",
            "Independence of the safety/governance function from commercial pressure",
        ],
    },
    {
        "key": "accountability",
        "title": "Accountability & Transparency",
        "scale": "(1=auditable and answerable, 10=opaque and effectively unaccountable)",
        "criteria": [
            "Disclosure of capabilities, limitations, incidents and evaluation results",
            "Existence of an external party with standing to demand answers",
            "Availability of recourse for people affected by its systems",
            "Jurisdictional reach of any body able to compel disclosure",
        ],
    },
    {
        "key": "concentration",
        "title": "Market & Infrastructure Concentration",
        "scale": "(1=one of many substitutable providers, 10=chokepoint others cannot route around)",
        "criteria": [
            "Share of the relevant market, compute, data or distribution that it controls",
            "Switching cost and availability of substitutes for parties that depend on it",
            "Degree of vertical integration across the stack",
            "Whether downstream actors have built critical dependence on it",
        ],
    },
]


# Every dimension definition, keyed — the composition source for the sets below
# and the lookup risk_analyzer uses to find a dimension by key.
DIMENSION_REGISTRY: dict[str, dict] = {
    dim["key"]: dim
    for dim in [*RISK_DIMENSIONS, *_INSTRUMENT_ONLY, *_ORGANIZATION_ONLY]
}


def _compose(*keys: str) -> list[dict]:
    return [DIMENSION_REGISTRY[k] for k in keys]


# Subject type → the dimensions scored for it. Sets are kept at 7 dimensions
# each so cost and latency don't change with subject type: run_risk_analysis
# makes one parallel LLM call per dimension.
#
# use_case and supply_chain share the system set for now. supply_chain is the
# weaker fit — a set built around concentration and dependency would suit it
# better — but a bespoke set per type would be five sets to maintain, so it
# starts here and moves out if the assessments show strain.
DIMENSION_SETS: dict[str, list[dict]] = {
    "technology": RISK_DIMENSIONS,
    "use_case": RISK_DIMENSIONS,
    "supply_chain": RISK_DIMENSIONS,
    "policy": _compose(
        "enforcement", "uncertainty", "fragmentation", "burden",
        "equity", "geopolitical", "systemic",
    ),
    "actor": _compose(
        "governance_maturity", "accountability", "concentration",
        "misuse", "equity", "geopolitical", "systemic",
    ),
}


def dimensions_for(analysis_type: str) -> list[dict]:
    """The dimension set to score for a subject type.

    Falls back to RISK_DIMENSIONS for anything unrecognised, which is required
    rather than defensive: `analysis_type` is deliberately a free-form string
    (see schemas/analysis.py) that is injected verbatim into the prompt, so it
    is not constrained to the keys of DIMENSION_SETS.
    """
    return DIMENSION_SETS.get(analysis_type, RISK_DIMENSIONS)
