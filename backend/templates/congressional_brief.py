CONGRESSIONAL_BRIEF_SYSTEM = """You are a senior policy analyst at an AI policy think tank writing a congressional briefing on behalf of technical experts. Your audience is congressional staff and members of Congress who are intelligent but not technical specialists.

Your writing must be:
- Precise: every claim is accurate and sourced
- Accessible: no unexplained technical jargon
- Actionable: concrete policy options, not vague recommendations
- Balanced: present multiple perspectives on contested questions
- Appropriately cautious: distinguish established facts from projections

Format in clean Markdown. Use numbered lists for legislative options. Keep language at a senior professional level — clear, authoritative, and direct."""

CONGRESSIONAL_BRIEF_SECTIONS = [
    {
        "key": "executive_summary",
        "title": "Executive Summary",
        "instructions": (
            "Write a 150-200 word executive summary that a busy member of Congress "
            "could read in under 60 seconds. Cover: (1) what the issue is, "
            "(2) why it matters for policy now, (3) 2-3 top recommendations. "
            "Make every sentence count."
        ),
        "summarize_body": True,
    },
    {
        "key": "background",
        "title": "Background & Context",
        "instructions": (
            "Write 300-400 words of background. Cover: what the technology/issue is, "
            "its current state of development or deployment, what Congress has previously "
            "done in this space, and the international context if relevant."
        ),
    },
    {
        "key": "key_findings",
        "title": "Key Findings",
        "instructions": (
            "Present 4-6 key findings as bold-headline bullet sections (300-500 words total). "
            "Each finding: bold headline claim, then 2-3 supporting sentences with citations. "
            "Cite sources inline. Focus on findings most relevant to policy action."
        ),
    },
    {
        "key": "policy_implications",
        "title": "Policy Implications",
        "instructions": (
            "Analyze (300-400 words) what these findings mean for policy. "
            "What are the risks of inaction? What is the time horizon? "
            "Who else (allies, industry, states) is acting? "
            "What federal authorities already exist vs. what requires new legislation?"
        ),
    },
    {
        "key": "legislative_options",
        "title": "Legislative Options",
        "instructions": (
            "Present 3-4 numbered legislative options, ranging from narrow/near-term "
            "to comprehensive/longer-term (300-400 words total). For each: "
            "what it does, pros, cons, and any relevant precedent or existing bill."
        ),
    },
    {
        "key": "stakeholder_perspectives",
        "title": "Stakeholder Perspectives",
        "instructions": (
            "In 200-250 words, briefly note where key stakeholders agree and disagree: "
            "industry, civil society/advocates, academic researchers, U.S. allies, "
            "and relevant federal agencies. Be factual and balanced."
        ),
    },
    {
        "key": "recommended_next_steps",
        "title": "Recommended Next Steps",
        "instructions": (
            "Provide 2-3 specific, near-term actions Congress could take "
            "(100-150 words): hearings to hold, agencies to query for information, "
            "studies to commission, or near-term legislative vehicles to consider."
        ),
    },
    {
        "key": "sources",
        "title": "Sources & Further Reading",
        "instructions": (
            "List all sources cited in the brief as a numbered reference list. "
            "Format: [N] Author/Organization. Title. Date. URL. "
            "Then list 3-5 additional recommended readings for deeper background."
        ),
    },
]
