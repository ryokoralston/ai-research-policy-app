POLICY_MEMO_SYSTEM = """You are a senior national security and technology policy analyst writing a policy memorandum for executive branch officials or NSC staff. Your writing is direct, structured, and action-oriented.

Style guidelines:
- Start with the bottom line — the recommendation comes first
- Be direct: senior officials have limited time
- Use crisp, clear language — no academic hedging without reason
- Options are clearly delineated with explicit tradeoffs
- Format cleanly in Markdown with clear section headers"""

POLICY_MEMO_SECTIONS = [
    {
        "key": "subject",
        "title": "SUBJECT",
        "instructions": (
            "Write a single, specific subject line for the memo "
            "(e.g., 'Policy Options for AI Export Control Tightening'). "
            "One sentence, no more."
        ),
    },
    {
        "key": "bluf",
        "title": "BOTTOM LINE UP FRONT",
        "instructions": (
            "State the single most important recommendation or conclusion in 40-60 words. "
            "This is what the reader should remember if they read nothing else."
        ),
        "summarize_body": True,
    },
    {
        "key": "background",
        "title": "Background",
        "instructions": (
            "Provide 150-250 words of essential background. "
            "Focus on what changed or what new information is available that prompted this memo. "
            "What has the U.S. government previously done? What do adversaries/allies know?"
        ),
    },
    {
        "key": "analysis",
        "title": "Analysis",
        "instructions": (
            "Provide 300-400 words of structured analysis. "
            "Organize around the 2-4 most important questions decision-makers face. "
            "Present evidence and its implications directly. "
            "Use sub-headers for each key question."
        ),
    },
    {
        "key": "options",
        "title": "Options",
        "instructions": (
            "Present exactly 3 options as a structured comparison (250-350 words). "
            "For each option provide: what it does, pros, cons, and estimated timeline. "
            "Label them Option 1 (Status Quo), Option 2 (Moderate Action), Option 3 (Bold Action)."
        ),
    },
    {
        "key": "recommendation",
        "title": "Recommendation",
        "instructions": (
            "State your recommendation clearly (100-150 words). "
            "Explain why this option best balances the risks and objectives. "
            "Note the 1-2 most important conditions or assumptions underlying this recommendation."
        ),
    },
    {
        "key": "implementation",
        "title": "Implementation Considerations",
        "instructions": (
            "In 100-150 words, briefly flag: which agencies need to act, "
            "any interagency coordination required, key timeline milestones, "
            "and the most important risk to watch. Keep it brief — details in annexes."
        ),
    },
]
