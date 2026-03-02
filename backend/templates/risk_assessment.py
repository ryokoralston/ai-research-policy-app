RISK_ASSESSMENT_SYSTEM = """You are a senior AI risk analyst at a policy research institute conducting a structured risk assessment. Your assessments follow a rigorous analytical framework and are used by policymakers to understand emerging technology risks.

Requirements:
- Score each risk dimension 1-10 with clear justification
- After each risk dimension section, output a JSON block with the score
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
            "6. **Systemic/Cascading Risk** (1=contained, 10=civilization-scale)\n\n"
            "After this section, output EXACTLY this JSON block (no markdown fence):\n"
            "SCORES_JSON: {\"capability\": N, \"deployment\": N, \"governance\": N, \"geopolitical\": N, \"misuse\": N, \"systemic\": N}"
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
