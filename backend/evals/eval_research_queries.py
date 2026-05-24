"""
Prompt Evaluation: Research Query Decomposition
================================================
Tests the query decomposition prompt in research_agent.py using
code-based grading (no extra API calls needed for scoring).

Eval Workflow (from course):
  1. Test dataset      — research questions the app might receive
  2. Run prompt        — call generate_text() with current prompt
  3. Grade output      — validate JSON structure, count, and quality
  4. Report scores     — average across all test cases
  5. Iterate           — change the prompt, re-run, compare scores

Usage:
  cd backend
  source venv/bin/activate
  python -m evals.eval_research_queries
"""

import asyncio
import json
import sys
from pathlib import Path

# Ensure backend/ is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.anthropic_client import generate_text

# ── Test Dataset ──────────────────────────────────────────────────────────────
# A collection of research questions the app might receive.
# Tip: use Claude to generate more test cases (see course note "Generating Test Datasets").

TEST_DATASET = [
    {"task": "What are the main AI governance risks from autonomous weapons systems?"},
    {"task": "How is the EU AI Act being implemented across member states?"},
    {"task": "What is the current state of AI regulation in China?"},
    {"task": "What are the economic impacts of large language models on employment?"},
    {"task": "How are AI models being used in clinical healthcare decision-making?"},
    {"task": "What oversight mechanisms exist for AI in the US federal government?"},
    {"task": "What are the risks of AI-generated disinformation in elections?"},
    {"task": "How are technology companies self-regulating AI development?"},
]


# ── Prompt Under Test ─────────────────────────────────────────────────────────
# This is the exact prompt from research_agent.py.
# Change it here and re-run to compare scores.

PROMPT_VERSION = "v2"

def build_prompt(task: str) -> str:
    return (
        f"You are a policy research assistant. Given this research question, "
        f"generate exactly 3 specific search queries that together provide comprehensive coverage.\n\n"
        f"Research question: {task}\n\n"
        f'Return ONLY a JSON array of 3 strings, like: ["query1", "query2", "query3"]'
    )


# ── Run Prompt ────────────────────────────────────────────────────────────────

async def run_prompt(test_case: dict) -> str:
    """Call generate_text() with the current prompt and return raw output."""
    return await generate_text(
        build_prompt(test_case["task"]),
        temperature=0.2,
        prefill="```json",
        stop_sequences=["```"],
    )


# ── Code-Based Grader ─────────────────────────────────────────────────────────
# Returns a score 0–10 based on structural validity.
# No extra API call needed — fast and deterministic.

def grade_output(output: str) -> dict:
    """
    Scoring rubric (total = 10):
      3 pts — valid JSON
      2 pts — output is a list/array
      2 pts — exactly 3 queries returned
      2 pts — all items are strings
      1 pt  — all queries are substantial (>15 chars each)
    """
    score = 0
    reasons = []

    # Strip prefill header before parsing
    json_str = output[len("```json"):].strip() if output.startswith("```json") else output.strip()

    # Check 1: Valid JSON (3 pts)
    try:
        queries = json.loads(json_str.strip())
        score += 3
        reasons.append("✅ Valid JSON  (+3)")
    except json.JSONDecodeError as e:
        reasons.append(f"❌ Invalid JSON: {e}  (+0)")
        return {"score": 0, "max_score": 10, "reasons": reasons, "queries": None}

    # Check 2: Is a list (2 pts)
    if isinstance(queries, list):
        score += 2
        reasons.append("✅ Output is a list  (+2)")
    else:
        reasons.append(f"❌ Expected list, got {type(queries).__name__}  (+0)")
        return {"score": score, "max_score": 10, "reasons": reasons, "queries": queries}

    # Check 3: Exactly 3 items (2 pts, partial credit for non-empty)
    if len(queries) == 3:
        score += 2
        reasons.append("✅ Exactly 3 queries  (+2)")
    elif len(queries) > 0:
        score += 1
        reasons.append(f"⚠️  {len(queries)} queries (expected 3)  (+1)")
    else:
        reasons.append("❌ Empty list  (+0)")

    # Check 4: All items are strings (2 pts)
    if all(isinstance(q, str) for q in queries):
        score += 2
        reasons.append("✅ All items are strings  (+2)")
    else:
        reasons.append("❌ Some items are not strings  (+0)")

    # Check 5: Queries are substantial — >15 chars (1 pt)
    if queries and all(len(q.strip()) > 15 for q in queries if isinstance(q, str)):
        score += 1
        reasons.append("✅ All queries are substantial (>15 chars)  (+1)")
    else:
        reasons.append("⚠️  Some queries are too short  (+0)")

    return {"score": score, "max_score": 10, "reasons": reasons, "queries": queries}


# ── Run Single Test Case ──────────────────────────────────────────────────────

async def run_test_case(test_case: dict, index: int, total: int) -> dict:
    print(f"\nTest {index}/{total}: {test_case['task'][:60]}...")
    output = await run_prompt(test_case)
    grade = grade_output(output)

    for reason in grade["reasons"]:
        print(f"  {reason}")

    if grade["queries"]:
        for i, q in enumerate(grade["queries"], 1):
            print(f"  [{i}] {q}")

    print(f"  Score: {grade['score']}/{grade['max_score']}")
    return {
        "task": test_case["task"],
        "score": grade["score"],
        "max_score": grade["max_score"],
        "reasons": grade["reasons"],
        "queries": grade["queries"],
    }


# ── Run Full Eval ─────────────────────────────────────────────────────────────

async def run_eval():
    print(f"{'='*60}")
    print(f"  Prompt Eval: Research Query Decomposition  ({PROMPT_VERSION})")
    print(f"  Dataset size: {len(TEST_DATASET)} test cases")
    print(f"{'='*60}")

    results = []
    for i, test_case in enumerate(TEST_DATASET, 1):
        result = await run_test_case(test_case, i, len(TEST_DATASET))
        results.append(result)

    # Summary
    avg = sum(r["score"] for r in results) / len(results)
    max_score = results[0]["max_score"]
    pct = avg / max_score * 100

    print(f"\n{'='*60}")
    print(f"  Prompt version : {PROMPT_VERSION}")
    print(f"  Average score  : {avg:.1f} / {max_score}  ({pct:.0f}%)")
    print(f"  Pass rate      : {sum(1 for r in results if r['score'] >= 8)}/{len(results)} (score ≥ 8)")
    print(f"{'='*60}")
    return results


if __name__ == "__main__":
    asyncio.run(run_eval())
