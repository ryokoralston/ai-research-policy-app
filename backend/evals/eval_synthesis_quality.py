"""
Prompt Evaluation: Research Synthesis Quality
==============================================
Tests the synthesis prompt in research_agent.py using
model-based grading (LLM-as-judge).

Why model grading here?
  Code-based grading checks structure (is it JSON? is it a list?).
  Synthesis quality — clarity, citation usage, policy relevance —
  requires subjective judgment that only a model can assess.

Eval Workflow:
  1. Dataset      — policy questions + pre-fetched source summaries
  2. Run prompt   — call generate_text() with the synthesis prompt
  3. Grade output — Claude grades the synthesis 1–10 with reasons
  4. Report       — average score across all test cases
  5. Iterate      — change synthesis prompt, re-run, compare

Usage:
  cd backend
  source venv/bin/activate
  python -m evals.eval_synthesis_quality
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.anthropic_client import generate_text

# ── Test Dataset ──────────────────────────────────────────────────────────────
# Each case has a research query + a small set of pre-written source summaries
# (simulating what Tavily + per-source summarization would return).
# This lets the eval run without live web searches.

TEST_DATASET = [
    {
        "query": "What are the main risks of AI-generated disinformation in elections?",
        "sources": [
            {
                "order": 1,
                "title": "AI Deepfakes and Election Integrity",
                "url": "https://example.com/deepfakes",
                "summary": (
                    "Researchers found that AI-generated deepfake videos of politicians "
                    "spread 6x faster than corrections on social media. "
                    "Key claims: (1) Detection tools lag behind generation tools by 12-18 months. "
                    "(2) Voters exposed to deepfakes show 23% lower candidate trust. "
                    "Relevant: directly addresses AI disinformation risk in elections."
                ),
            },
            {
                "order": 2,
                "title": "LLM-Generated Synthetic Media: Policy Implications",
                "url": "https://example.com/llm-policy",
                "summary": (
                    "Large language models can generate targeted political messaging "
                    "at scale for under $100. "
                    "Key claims: (1) Micro-targeted disinformation is now accessible to small actors. "
                    "(2) Current FEC regulations do not cover AI-generated content. "
                    "Relevant: highlights governance gap in election security."
                ),
            },
            {
                "order": 3,
                "title": "Countermeasures Against AI Election Interference",
                "url": "https://example.com/countermeasures",
                "summary": (
                    "Watermarking and provenance tracking are the most promising "
                    "near-term countermeasures. "
                    "Key claims: (1) C2PA standard adopted by major platforms in 2024. "
                    "(2) Mandatory disclosure laws passed in 4 US states. "
                    "Relevant: identifies existing safeguards and their limitations."
                ),
            },
        ],
    },
    {
        "query": "How is the EU AI Act being implemented across member states?",
        "sources": [
            {
                "order": 1,
                "title": "EU AI Act Implementation Tracker",
                "url": "https://example.com/eu-ai-act",
                "summary": (
                    "Only 8 of 27 EU member states have designated national AI authorities "
                    "as required by the Act. "
                    "Key claims: (1) Deadline for high-risk AI compliance is August 2026. "
                    "(2) SMEs report compliance costs averaging €250,000. "
                    "Relevant: shows uneven implementation progress."
                ),
            },
            {
                "order": 2,
                "title": "Germany and France Lead EU AI Governance",
                "url": "https://example.com/de-fr-ai",
                "summary": (
                    "Germany's Federal AI Office and France's CNIL are the most active "
                    "national enforcers. "
                    "Key claims: (1) Germany issued first AI Act enforcement guidance in March 2025. "
                    "(2) France prioritizing biometric surveillance restrictions. "
                    "Relevant: illustrates divergent national implementation approaches."
                ),
            },
            {
                "order": 3,
                "title": "SME Challenges Under EU AI Act",
                "url": "https://example.com/sme-ai",
                "summary": (
                    "Small and medium enterprises face disproportionate compliance burdens. "
                    "Key claims: (1) 67% of EU SMEs unaware of their obligations under the Act. "
                    "(2) Commission sandbox program oversubscribed by 4x. "
                    "Relevant: highlights equity concerns in AI regulation."
                ),
            },
        ],
    },
    {
        "query": "What oversight mechanisms exist for AI in the US federal government?",
        "sources": [
            {
                "order": 1,
                "title": "Executive Order 14110 Implementation Status",
                "url": "https://example.com/eo14110",
                "summary": (
                    "Most agencies met EO 14110 reporting deadlines but implementation "
                    "of safety standards varies widely. "
                    "Key claims: (1) 18 agencies submitted AI use inventories. "
                    "(2) DOD and IC face separate AI governance tracks. "
                    "Relevant: maps federal AI oversight landscape."
                ),
            },
            {
                "order": 2,
                "title": "NIST AI Risk Management Framework Adoption",
                "url": "https://example.com/nist-rmf",
                "summary": (
                    "NIST AI RMF adopted by 340+ organizations, but federal mandate "
                    "remains voluntary. "
                    "Key claims: (1) OMB M-24-10 requires agencies to designate Chief AI Officers. "
                    "(2) GAO found 17 of 23 large agencies lack AI inventory processes. "
                    "Relevant: shows gap between policy intent and implementation."
                ),
            },
            {
                "order": 3,
                "title": "Congressional AI Oversight Efforts",
                "url": "https://example.com/congress-ai",
                "summary": (
                    "Congressional AI caucus has 150 members but no major AI legislation "
                    "has passed since 2023. "
                    "Key claims: (1) Senate AI Working Group released roadmap in May 2024. "
                    "(2) Bipartisan agreement exists on national security AI risks. "
                    "Relevant: legislative oversight context."
                ),
            },
        ],
    },
]


# ── Prompt Under Test ─────────────────────────────────────────────────────────

PROMPT_VERSION = "v3"

def build_synthesis_prompt(query: str, sources: list[dict]) -> str:
    sources_text = "\n\n".join(
        f"[Source {s['order']}] {s['title']} ({s['url']})\n{s['summary']}"
        for s in sources
    )
    return (
        f"Research question: {query}\n\n"
        f"You have analyzed {len(sources)} sources. Below are their summaries:\n\n"
        f"{sources_text}\n\n"
        f"Write a comprehensive research synthesis that includes:\n"
        f"## Key Findings\n(3-5 bullet points with [Source N] citations)\n\n"
        f"## Areas of Consensus\n(What sources agree on)\n\n"
        f"## Areas of Uncertainty or Debate\n(Contested claims, conflicting evidence)\n\n"
        f"## Evidence Gaps\n(Important questions the available sources do not answer)\n\n"
        f"## Policy Recommendations\n"
        f"(3-4 concrete, actionable recommendations for policymakers. "
        f"For each: state the specific action, identify who should implement it "
        f"— Congress, federal agency, international body, or industry — "
        f"and explain what risk it addresses. "
        f"Prioritize from most to least feasible.)\n\n"
        f"## Recommended Further Research\n"
        f"(2-3 specific research directions with clear policy relevance)\n\n"
        f"Cite sources inline as [Source N] throughout. "
        f"Every claim must reference at least one source."
    )

SYNTHESIS_SYSTEM = (
    "You are a senior AI policy analyst at a leading think tank. "
    "Your syntheses are used to brief members of Congress and senior policy officials. "
    "Write in a clear, precise, and authoritative tone. "
    "Every claim must be supported by the sources provided. "
    "Distinguish clearly between established facts and projections or opinions."
)


# ── Run Prompt ────────────────────────────────────────────────────────────────

async def run_prompt(test_case: dict) -> str:
    """Generate a synthesis for the given query + sources."""
    prompt = build_synthesis_prompt(test_case["query"], test_case["sources"])
    # max_tokens=8192: v3 prompt added Policy Recommendations section;
    # default 4096 caused truncation mid-sentence before reaching that section
    return await generate_text(
        prompt, system=SYNTHESIS_SYSTEM, temperature=0.7, max_tokens=8192
    )


# ── Model-Based Grader (LLM-as-judge) ────────────────────────────────────────
# Claude reads the question + synthesis and scores it 1–10.
# Uses prefill + stop to get clean JSON — same structured data technique.

GRADER_SYSTEM = (
    "You are an expert evaluator of AI policy research. "
    "You assess research syntheses for clarity, accuracy, citation usage, and policy relevance. "
    "Be strict but fair. Score only what is present in the synthesis."
)

async def grade_output(query: str, synthesis: str) -> dict:
    """
    Ask Claude to grade the synthesis on four dimensions (1–10 each):
      - citation_use    : Are [Source N] citations used correctly and consistently?
      - coverage        : Does the synthesis cover Key Findings, Consensus, Gaps, etc.?
      - clarity         : Is the writing clear and policy-appropriate?
      - actionability   : Are the recommendations specific and useful?

    Returns average score (0–10) plus per-dimension breakdown.
    """
    grader_prompt = (
        f"<task>\n{query}\n</task>\n\n"
        f"<solution>\n{synthesis[:16000]}\n</solution>\n\n"
        f"Score this synthesis on four dimensions (1=poor, 10=excellent):\n"
        f"1. citation_use   — Are [Source N] citations used correctly throughout?\n"
        f"2. coverage       — Are all required sections present and substantive?\n"
        f"3. clarity        — Is the writing clear, precise, and policy-appropriate?\n"
        f"4. actionability  — Are the recommendations specific and useful?\n\n"
        f"For each dimension provide:\n"
        f"- strengths: 1-2 specific things done well\n"
        f"- weaknesses: 1-2 specific areas for improvement\n"
        f"- score: number 1-10\n\n"
        f"Also provide an overall reasoning string summarizing the synthesis quality.\n\n"
        f'Return JSON with keys: "citation_use", "coverage", "clarity", "actionability" '
        f'(each an object with "strengths" array, "weaknesses" array, "score" int), '
        f'plus a top-level "reasoning" string.'
    )

    try:
        raw = await generate_text(
            grader_prompt,
            system=GRADER_SYSTEM,
            temperature=0.0,        # fully deterministic — grading must be consistent
            prefill="```json",
            stop_sequences=["```"],
        )
        json_str = raw[len("```json"):].strip()
        result = json.loads(json_str)

        dims = ("citation_use", "coverage", "clarity", "actionability")
        # Explicit KeyError check: report which dimension is missing
        for k in dims:
            if k not in result:
                raise KeyError(f"Grader response missing dimension: '{k}'")
            if "score" not in result[k]:
                raise KeyError(f"Grader dimension '{k}' missing 'score' key")
        scores = [result[k]["score"] for k in dims]
        result["average"] = sum(scores) / len(scores)
        return result
    except Exception as e:
        print(f"  ⚠️  Grader error: {e}")
        return {
            "citation_use": {"score": 0, "strengths": [], "weaknesses": []},
            "coverage":     {"score": 0, "strengths": [], "weaknesses": []},
            "clarity":      {"score": 0, "strengths": [], "weaknesses": []},
            "actionability":{"score": 0, "strengths": [], "weaknesses": []},
            "average": 0, "reasoning": f"Grader error: {e}"
        }


# ── Run Single Test Case ──────────────────────────────────────────────────────

async def run_test_case(test_case: dict, index: int, total: int) -> dict:
    print(f"\nTest {index}/{total}: {test_case['query'][:60]}...")

    synthesis = await run_prompt(test_case)
    grade = await grade_output(test_case["query"], synthesis)

    for dim in ("citation_use", "coverage", "clarity", "actionability"):
        d = grade[dim]
        score = d["score"] if isinstance(d, dict) else d
        strength = d["strengths"][0] if isinstance(d, dict) and d["strengths"] else ""
        weakness = d["weaknesses"][0] if isinstance(d, dict) and d["weaknesses"] else ""
        print(f"  {dim:<16}: {score}/10")
        if strength: print(f"    + {strength[:80]}")
        if weakness: print(f"    - {weakness[:80]}")
    print(f"  Average       : {grade['average']:.1f}/10")
    print(f"  Reasoning     : {str(grade.get('reasoning', ''))[:120]}")

    return {
        "query": test_case["query"],
        "grade": grade,
        "synthesis_preview": synthesis[:200],
    }


# ── Run Full Eval ─────────────────────────────────────────────────────────────

async def run_eval():
    print(f"{'='*60}")
    print(f"  Prompt Eval: Research Synthesis Quality  ({PROMPT_VERSION})")
    print(f"  Dataset size: {len(TEST_DATASET)} test cases")
    print(f"  Grader: Claude (model-based / LLM-as-judge)")
    print(f"{'='*60}")

    results = []
    for i, test_case in enumerate(TEST_DATASET, 1):
        result = await run_test_case(test_case, i, len(TEST_DATASET))
        results.append(result)

    # Summary
    avg = sum(r["grade"]["average"] for r in results) / len(results)
    pass_rate = sum(1 for r in results if r["grade"]["average"] >= 7)

    print(f"\n{'='*60}")
    print(f"  Prompt version : {PROMPT_VERSION}")
    print(f"  Average score  : {avg:.1f} / 10  ({avg*10:.0f}%)")
    print(f"  Pass rate      : {pass_rate}/{len(results)} (avg >= 7)")

    # Per-dimension averages
    for dim in ("citation_use", "coverage", "clarity", "actionability"):
        d_vals = [r["grade"][dim] for r in results]
        dim_avg = sum(v["score"] if isinstance(v, dict) else v for v in d_vals) / len(d_vals)
        print(f"  {dim:<16}: {dim_avg:.1f}/10")
    print(f"{'='*60}")

    return results


if __name__ == "__main__":
    asyncio.run(run_eval())
