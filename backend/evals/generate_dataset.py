"""
Generating Test Datasets with Claude
=====================================
Uses Claude to automatically generate diverse test cases for evals.

Why generate datasets with Claude?
  - Hand-crafting 8 cases takes time and reflects human bias.
  - Claude can produce dozens of varied, realistic cases in seconds.
  - Claude can deliberately include edge cases and adversarial inputs
    that humans might overlook.

Two-stage generation (from the PromptEvaluator notebook):
  Stage 1 — generate_ideas(): ask Claude for N short, distinct *ideas*
            (one line each). High temperature → maximum diversity.
  Stage 2 — idea_to_case():   expand each idea into a full test case.
            Run concurrently (bounded) so N cases generate in parallel.
  Splitting "be diverse" from "be detailed" yields less repetitive datasets
  than asking for N full cases in one shot.

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
from evals.template import render

OUTPUT_FILE = Path(__file__).parent / "eval_dataset_generated.json"

# Bound concurrency for stage-2 expansion, like the notebook's
# ThreadPoolExecutor(max_workers=3). Keeps us under rate limits.
MAX_CONCURRENT = 3


async def _gen_json(prompt: str, temperature: float):
    """Call the model and parse a JSON body returned inside a ```json fence."""
    raw = await generate_text(
        prompt, temperature=temperature, prefill="```json", stop_sequences=["```"]
    )
    return json.loads(raw[len("```json"):].strip())


# ── Stage 1: Ideas ────────────────────────────────────────────────────────────

_IDEAS_TEMPLATE = (
    "You are a test scenario designer for an AI policy research assistant.\n\n"
    "Generate {n} unique, diverse ideas for {kind}. Each idea is a SHORT "
    "one-line description of a distinct scenario — not the full case yet.\n\n"
    "Cover a mix of:\n{mix}\n\n"
    'Return a JSON array of {n} strings. '
    'Example: ["idea one", "idea two", ...]'
)


async def generate_ideas(n: int, kind: str, mix: str) -> list[str]:
    """Stage 1: ask Claude for n short, distinct idea strings (high diversity)."""
    prompt = render(_IDEAS_TEMPLATE, {"n": n, "kind": kind, "mix": mix})
    ideas = await _gen_json(prompt, temperature=1.0)  # high temp = varied
    return [str(i) for i in ideas]


# ── Stage 2: Expand one idea → one full test case ─────────────────────────────

_DECOMP_CASE_TEMPLATE = (
    "Expand this idea into ONE test case for an AI policy research assistant "
    "that decomposes a question into search queries.\n\n"
    "<idea>\n{idea}\n</idea>\n\n"
    "Write a realistic, specific research question matching the idea.\n\n"
    'Return a JSON object: {{"task": "the research question"}}'
)


async def _decomp_case_from_idea(idea: str) -> dict:
    case = await _gen_json(
        render(_DECOMP_CASE_TEMPLATE, {"idea": idea}), temperature=0.7
    )
    if isinstance(case, list):   # model sometimes wraps the object — unwrap
        case = case[0]
    return case


async def generate_query_decomp_cases(n: int = 20) -> list[dict]:
    """
    Generate test cases for eval_research_queries.py via two-stage generation.
    Stage 1 produces n diverse ideas; stage 2 expands each into a {"task": ...}
    case concurrently (bounded by MAX_CONCURRENT). asyncio.gather preserves
    order, so output order tracks the ideas list.
    """
    ideas = await generate_ideas(
        n,
        kind="testing a query-decomposition prompt — realistic AI policy "
             "research questions a congressional researcher might ask",
        mix=(
            "- Straightforward policy questions (about 60%)\n"
            "- Narrow technical questions (about 20%)\n"
            "- Ambiguous or very broad questions (about 10%) — edge cases\n"
            "- Non-English questions, e.g. Japanese or French (about 10%) — edge cases"
        ),
    )

    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def _bounded(idea: str) -> dict:
        async with sem:
            return await _decomp_case_from_idea(idea)

    return list(await asyncio.gather(*(_bounded(i) for i in ideas)))


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
