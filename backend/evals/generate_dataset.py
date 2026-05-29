"""
Generating Test Datasets with Claude
=====================================
Uses Claude to automatically generate diverse test cases for evals.

Why generate datasets with Claude?
  - Hand-crafting 8 cases takes time and reflects human bias.
  - Claude can produce dozens of varied, realistic cases in seconds.
  - Claude can deliberately include edge cases and adversarial inputs
    that humans might overlook.

Output: eval_dataset_generated.json  (ready to use in eval scripts)

Usage:
  cd backend
  source venv/bin/activate
  python -m evals.generate_dataset
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.anthropic_client import generate_text

OUTPUT_FILE = Path(__file__).parent / "eval_dataset_generated.json"


# ── Generator Prompts ─────────────────────────────────────────────────────────

async def generate_query_decomp_cases(n: int = 20) -> list[dict]:
    """
    Generate test cases for eval_research_queries.py.
    Asks Claude to produce varied AI policy research questions,
    including edge cases that might trip up the decomposition prompt.
    """
    prompt = (
        f"You are helping build a test dataset for an AI policy research assistant.\n\n"
        f"Generate {n} diverse research questions that the assistant might receive. "
        f"Include a mix of:\n"
        f"- Straightforward policy questions (about 60%)\n"
        f"- Narrow technical questions (about 20%)\n"
        f"- Ambiguous or very broad questions (about 10%) — edge cases\n"
        f"- Non-English questions, e.g. in Japanese or French (about 10%) — edge cases\n\n"
        f"Each question should be realistic for a congressional policy researcher.\n\n"
        f'Return a JSON array of objects with key "task". '
        f'Example: [{{"task": "What are the risks of..."}}, ...]'
    )

    raw = await generate_text(
        prompt,
        temperature=1.0,        # high diversity — we want varied questions
        prefill="```json",
        stop_sequences=["```"],
    )
    json_str = raw[len("```json"):].strip()
    cases = json.loads(json_str)
    return cases


async def generate_synthesis_cases(n: int = 10) -> list[dict]:
    """
    Generate test cases for eval_synthesis_quality.py.
    Each case needs a query + 3 plausible source summaries.
    """
    prompt = (
        f"You are helping build a test dataset for an AI policy research synthesis evaluator.\n\n"
        f"Generate {n} test cases. Each test case has:\n"
        f"1. A research query (AI policy topic)\n"
        f"2. Exactly 3 source summaries (simulating web search results)\n\n"
        f"Each source summary should include:\n"
        f"- A plausible title and URL\n"
        f"- 2-3 sentences of content with specific facts/statistics\n"
        f"- Key claims (2 bullet points)\n"
        f"- A relevance sentence\n\n"
        f"Include varied topics: EU regulation, US federal AI, China AI policy, "
        f"healthcare AI, autonomous weapons, labor market impacts, etc.\n\n"
        f"Return a JSON array of objects with this structure:\n"
        f'{{"query": "...", "sources": ['
        f'{{"order": 1, "title": "...", "url": "...", "summary": "..."}}, '
        f'...]}}'
    )

    raw = await generate_text(
        prompt,
        temperature=1.0,
        prefill="```json",
        stop_sequences=["```"],
    )
    json_str = raw[len("```json"):].strip()
    cases = json.loads(json_str)
    return cases


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  Generating eval test datasets with Claude")
    print("=" * 60)

    # ── Query decomposition cases ─────────────────────────────
    print("\n[1/2] Generating query decomposition cases (n=20)...")
    query_cases = await generate_query_decomp_cases(n=20)
    print(f"  Generated: {len(query_cases)} cases")
    print("  Sample cases:")
    for c in query_cases[:3]:
        print(f"    - {c['task'][:70]}")
    print(f"    ... ({len(query_cases) - 3} more)")

    # ── Synthesis quality cases ───────────────────────────────
    print("\n[2/2] Generating synthesis quality cases (n=10)...")
    synthesis_cases = await generate_synthesis_cases(n=10)
    print(f"  Generated: {len(synthesis_cases)} cases")
    print("  Sample queries:")
    for c in synthesis_cases[:3]:
        print(f"    - {c['query'][:70]}")
    print(f"    ... ({len(synthesis_cases) - 3} more)")

    # ── Save to file ──────────────────────────────────────────
    output = {
        "query_decomposition": query_cases,
        "synthesis_quality": synthesis_cases,
    }
    OUTPUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False))

    print(f"\n{'=' * 60}")
    print(f"  Saved to: {OUTPUT_FILE}")
    print(f"  query_decomposition : {len(query_cases)} cases")
    print(f"  synthesis_quality   : {len(synthesis_cases)} cases")
    print(f"  Total               : {len(query_cases) + len(synthesis_cases)} cases")
    print(f"{'=' * 60}")
    print("\nNext step: replace TEST_DATASET in eval scripts with this file.")


if __name__ == "__main__":
    asyncio.run(main())
