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
            "For each of the 6 risk dimensions below, provide:\n"
            "- Score: X/10 (with brief justification in parentheses)\n"
            "- 2-3 sentence analysis\n\n"
            "Dimensions to assess:\n"
            "1. **Technical Capability Level** (1=minimal, 10=transformative/superhuman)\n"
            "2. **Deployment/Proliferation Speed** (1=slow/limited, 10=rapid/widespread)\n"
            "3. **Governance & Oversight Gap** (1=well-governed, 10=governance vacuum)\n"
            "4. **Geopolitical Risk Concentration** (1=distributed/benign, 10=concentrated/adversarial)\n"
            "5. **Misuse Potential** (1=difficult to misuse, 10=trivially weaponizable)\n"
            "6. **Systemic/Cascading Risk** (1=contained, 10=civilization-scale)"
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
# `key` MUST match the keys the scores-extraction prompt in run_risk_analysis
# asks generate_json() for: capability, deployment, governance, geopolitical,
# misuse, systemic. `title` matches the bold dimension names and `scale`
# matches the 1=.../10=... anchors previously embedded in the single prompt
# above — same wording, just split out per-dimension. `criteria` is new:
# 3-5 bullet points of specialized considerations unique to that dimension,
# so each parallel call gets a substantive, distinct rubric instead of the
# one-line description every dimension shared before.
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
