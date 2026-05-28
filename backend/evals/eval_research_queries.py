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
from evals.report import build_html_report, save_report

DATASET_FILE = Path(__file__).parent / "dataset_research_queries.json"

# ── Dataset Generator ─────────────────────────────────────────────────────────
# Uses claude-haiku-4-5 (fast + cheap) to auto-generate test cases.
# Run once with --generate to create dataset_research_queries.json,
# then subsequent runs load from file.

async def generate_dataset(n: int = 8) -> list[dict]:
    """Ask Haiku to generate n AI policy research questions for eval."""
    prompt = (
        f"Generate an evaluation dataset for a prompt evaluation. "
        f"The dataset will be used to evaluate prompts that decompose AI policy "
        f"research questions into specific web search queries.\n\n"
        f"Generate an array of JSON objects, each with:\n"
        f"- 'task': a realistic AI policy research question\n"
        f"- 'solution_criteria': a string describing what makes a good set of "
        f"search queries for this task (e.g. what topics, angles, or sources "
        f"the queries should collectively cover)\n\n"
        f"Focus on questions about: AI regulation, governance risks, "
        f"geopolitical AI competition, AI safety policy, sector-specific AI impacts.\n\n"
        f"Example:\n"
        f'{{"task": "What are the risks of AI in hiring?", '
        f'"solution_criteria": "Queries should cover bias/discrimination risks, '
        f'legal frameworks (EEOC, EU AI Act), and employer best practices"}}\n\n'
        f"Please generate {n} objects."
    )
    raw = await generate_text(
        prompt,
        model="claude-haiku-4-5-20251001",  # fast + cheap for data generation
        temperature=1.0,                     # high temp = more varied questions
        prefill="```json",
        stop_sequences=["```"],
    )
    json_str = raw[len("```json"):].strip()
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"generate_dataset: invalid JSON from model: {e}\nRaw: {raw[:200]}")


async def load_or_generate_dataset(force: bool = False) -> list[dict]:
    """Load dataset from file if it exists; otherwise generate and save it.
    Pass force=True to regenerate even if the file exists (--generate flag)."""
    if DATASET_FILE.exists() and not force:
        with open(DATASET_FILE) as f:
            return json.load(f)
    print("  Generating dataset with Haiku...")
    dataset = await generate_dataset()
    with open(DATASET_FILE, "w") as f:
        json.dump(dataset, f, indent=2)
    print(f"  Saved {len(dataset)} cases to {DATASET_FILE.name}")
    return dataset


# ── Fallback Dataset (used if --no-generate flag passed) ─────────────────────
TEST_DATASET = [
    {"task": "What are the main AI governance risks from autonomous weapons systems?",
     "solution_criteria": "Queries should cover accountability gaps, international law (CCW), and specific risk scenarios like target misidentification"},
    {"task": "How is the EU AI Act being implemented across member states?",
     "solution_criteria": "Queries should cover national authority designation, compliance timelines, and divergence between member states"},
    {"task": "What is the current state of AI regulation in China?",
     "solution_criteria": "Queries should cover algorithmic recommendation rules, generative AI regulations, and comparison with Western approaches"},
    {"task": "What are the economic impacts of large language models on employment?",
     "solution_criteria": "Queries should cover job displacement evidence, new job creation, wage effects, and sector-specific impacts"},
    {"task": "How are AI models being used in clinical healthcare decision-making?",
     "solution_criteria": "Queries should cover diagnostic AI, clinical decision support systems, regulatory approval (FDA), and patient safety concerns"},
    {"task": "What oversight mechanisms exist for AI in the US federal government?",
     "solution_criteria": "Queries should cover EO 14110, NIST AI RMF, OMB guidance, and agency-level implementation gaps"},
    {"task": "What are the risks of AI-generated disinformation in elections?",
     "solution_criteria": "Queries should cover deepfakes, LLM-generated content, detection tools, and existing legal/platform responses"},
    {"task": "How are technology companies self-regulating AI development?",
     "solution_criteria": "Queries should cover voluntary commitments, AI safety teams, industry consortia, and effectiveness evidence"},
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


# ── Model Grader (quality check) ─────────────────────────────────────────────
# Code grader checks structure; model grader checks content quality.
# Combined score = (code_score + model_score) / 2

async def grade_query_quality(task: str, queries: list[str],
                              solution_criteria: str = "") -> dict:
    """Ask Claude to score query relevance and specificity (1–10)."""
    if not queries:
        return {"score": 0, "reasoning": "No queries to evaluate"}

    queries_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(queries))
    criteria_block = (
        f"\n<criteria>\n{solution_criteria}\n</criteria>"
        if solution_criteria else ""
    )
    prompt = (
        f"You are evaluating search queries generated for an AI policy research task.\n\n"
        f"<task>\n{task}\n</task>"
        f"{criteria_block}\n\n"
        f"<queries>\n{queries_text}\n</queries>\n\n"
        f"Score these queries 1-10 based on:\n"
        f"- Relevance: Do they directly address the research task?\n"
        f"- Specificity: Are they precise enough to find useful sources?\n"
        f"- Coverage: Together, do they cover the angles specified in the criteria?\n\n"
        f"Provide strengths (1-2), weaknesses (1-2), and a score.\n"
        f'Return JSON with keys: "strengths" (array), "weaknesses" (array), '
        f'"reasoning" (string), "score" (int 1-10)'
    )
    try:
        raw = await generate_text(
            prompt,
            temperature=0.0,
            prefill="```json",
            stop_sequences=["```"],
        )
        json_str = raw[len("```json"):].strip()
        return json.loads(json_str)
    except Exception as e:
        return {"score": 5, "reasoning": f"Grader error: {e}",
                "strengths": [], "weaknesses": []}


# ── Run Single Test Case ──────────────────────────────────────────────────────

async def run_test_case(test_case: dict, index: int, total: int) -> dict:
    print(f"\nTest {index}/{total}: {test_case['task'][:60]}...")
    output = await run_prompt(test_case)

    # Code grader: structure check (0–10)
    code_grade = grade_output(output)
    for reason in code_grade["reasons"]:
        print(f"  {reason}")

    # Model grader: quality check (only if structure is valid)
    model_score = 0
    model_reasoning = ""
    if code_grade["queries"]:
        for i, q in enumerate(code_grade["queries"], 1):
            print(f"  [{i}] {q}")
        model_grade = await grade_query_quality(
            test_case["task"],
            code_grade["queries"],
            solution_criteria=test_case.get("solution_criteria", ""),
        )
        model_score = model_grade.get("score", 0)
        model_reasoning = model_grade.get("reasoning", "")
        preview = (model_reasoning[:60] + "...") if len(model_reasoning) > 60 else model_reasoning
        print(f"  Quality score : {model_score}/10  ({preview})")

    # Combined score = average of code + model
    combined = (code_grade["score"] + model_score) / 2
    print(f"  Code score    : {code_grade['score']}/10")
    print(f"  Combined      : {combined:.1f}/10")

    return {
        "task": test_case["task"],
        "code_score": code_grade["score"],
        "model_score": model_score,
        "combined_score": combined,
        "max_score": 10,
        "reasons": code_grade["reasons"],
        "queries": code_grade["queries"],
        "model_reasoning": model_reasoning,
        # keep "score" for summary compatibility
        "score": combined,
    }


# ── Run Full Eval ─────────────────────────────────────────────────────────────

async def run_eval(use_generated: bool = False):
    dataset = await load_or_generate_dataset(force=True) if use_generated else TEST_DATASET

    print(f"{'='*60}")
    print(f"  Prompt Eval: Research Query Decomposition  ({PROMPT_VERSION})")
    print(f"  Dataset size: {len(dataset)} test cases")
    print(f"  Dataset source: {'generated (Haiku)' if use_generated else 'hardcoded'}")
    print(f"{'='*60}")

    results = []
    for i, test_case in enumerate(dataset, 1):
        result = await run_test_case(test_case, i, len(dataset))
        results.append(result)

    avg_code     = sum(r["code_score"]     for r in results) / len(results)
    avg_model    = sum(r["model_score"]    for r in results) / len(results)
    avg_combined = sum(r["combined_score"] for r in results) / len(results)

    print(f"\n{'='*60}")
    print(f"  Prompt version   : {PROMPT_VERSION}")
    print(f"  Avg code score   : {avg_code:.1f} / 10  (structure)")
    print(f"  Avg model score  : {avg_model:.1f} / 10  (quality)")
    print(f"  Avg combined     : {avg_combined:.1f} / 10")
    print(f"  Pass rate        : {sum(1 for r in results if r['combined_score'] >= 8)}/{len(results)} (≥ 8)")
    print(f"{'='*60}")

    # HTML report
    entries = [
        {
            "scenario":   r["task"],
            "inputs":     {"task": r["task"][:80]},
            "criteria":   ["Valid JSON array", "Exactly 3 queries", "Each query >15 chars",
                           r.get("model_reasoning", "")[:60]],
            "output":     "\n".join(r["queries"]) if r.get("queries") else "(no queries)",
            "score":      r["combined_score"],
            "reasoning":  r.get("model_reasoning", "")[:200],
            "extra_info": {
                "code": f"{r['code_score']}/10",
                "model": f"{r['model_score']}/10",
            },
        }
        for r in results
    ]
    html = build_html_report(
        entries,
        title=f"Query Decomposition Eval  ({PROMPT_VERSION})",
        pass_threshold=8.0,
    )
    save_report(html, "evals/reports/research_queries.html")

    return results


if __name__ == "__main__":
    # python -m evals.eval_research_queries --generate  →  Haikuでデータセット生成
    # python -m evals.eval_research_queries             →  手書きデータセットを使用
    use_generated = "--generate" in sys.argv
    asyncio.run(run_eval(use_generated=use_generated))
