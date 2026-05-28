"""
Prompt Engineering: Iterative Improvement Pipeline
===================================================
Demonstrates the core prompt engineering loop:
  goal → naive prompt → evaluate → improve → re-evaluate → compare

Runs multiple prompt versions against the SAME dataset so scores are
directly comparable.  Prints a version-over-version comparison table.

This is the "full loop" the Skilljar lesson describes:
  1. Start with a naive/basic prompt (expect a low baseline)
  2. Identify weaknesses from eval output
  3. Apply techniques: specificity, few-shot hints, explicit format
  4. Re-evaluate and compare

Usage:
  cd backend
  source venv/bin/activate
  python -m evals.eval_prompt_versions
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.anthropic_client import generate_text
from evals.report import build_html_report, save_report

# ── Shared Dataset ────────────────────────────────────────────────────────────
# Same 4 cases for all versions → scores are directly comparable.

TEST_DATASET = [
    {
        "task": "What are the main AI governance risks from autonomous weapons systems?",
        "solution_criteria": (
            "Queries should cover accountability gaps, international law (CCW), "
            "and specific risk scenarios like target misidentification."
        ),
    },
    {
        "task": "How is the EU AI Act being implemented across member states?",
        "solution_criteria": (
            "Queries should cover national authority designation, compliance "
            "timelines, and divergence between member states."
        ),
    },
    {
        "task": "What are the risks of AI-generated disinformation in elections?",
        "solution_criteria": (
            "Queries should cover deepfakes, LLM-generated content, detection "
            "tools, and existing legal/platform responses."
        ),
    },
    {
        "task": "What oversight mechanisms exist for AI in the US federal government?",
        "solution_criteria": (
            "Queries should cover EO 14110, NIST AI RMF, OMB guidance, "
            "and agency-level implementation gaps."
        ),
    },
]


# ── Prompt Versions ───────────────────────────────────────────────────────────
# Each version is a dict with:
#   name        : display label
#   description : one-line summary of what changed
#   build       : function (task: str) -> str
#
# Technique progression mirrors the Skilljar lesson:
#   v1 → bare minimum (establishes low baseline)
#   v2 → add explicit count + format constraint
#   v3 → add persona + few-shot hint + coverage framing

PROMPT_VERSIONS = [
    {
        "name": "v1 (naive)",
        "description": "Bare minimum — no format, no count, no persona",
        "build": lambda task: (
            f"Generate search queries for this research question: {task}"
        ),
    },
    {
        "name": "v2 (structured)",
        "description": "Added: exact count (3), explicit JSON array format",
        "build": lambda task: (
            f"You are a policy research assistant. Given this research question, "
            f"generate exactly 3 specific search queries that together provide "
            f"comprehensive coverage.\n\n"
            f"Research question: {task}\n\n"
            f'Return ONLY a JSON array of 3 strings, like: ["query1", "query2", "query3"]'
        ),
    },
    {
        "name": "v3 (optimized)",
        "description": "Added: persona, few-shot hint, coverage framing, angle diversity",
        "build": lambda task: (
            f"You are a senior policy research librarian who designs expert-level "
            f"search strategies for think tanks and congressional briefings.\n\n"
            f"Given a research question, generate exactly 3 search queries. "
            f"Each query must target a DIFFERENT angle:\n"
            f"  1. Current evidence / empirical data\n"
            f"  2. Policy / regulatory / governance landscape\n"
            f"  3. Countermeasures / recommendations / best practices\n\n"
            f"Example (for 'AI bias in hiring'):\n"
            f'["AI bias discrimination evidence hiring algorithms 2024",\n'
            f' "EEOC EU AI Act employment algorithmic accountability regulation",\n'
            f' "bias mitigation fairness auditing AI hiring best practices"]\n\n'
            f"Research question: {task}\n\n"
            f'Return ONLY a JSON array of exactly 3 strings.'
        ),
    },
]


# ── Code Grader (structural) ──────────────────────────────────────────────────

def grade_structure(output: str) -> dict:
    """
    Rubric (total = 10):
      3 pts — valid JSON
      2 pts — is a list
      2 pts — exactly 3 items
      2 pts — all strings
      1 pt  — all queries > 15 chars
    """
    score = 0
    json_str = output[len("```json"):].strip() if output.startswith("```json") else output.strip()

    try:
        queries = json.loads(json_str)
        score += 3
    except json.JSONDecodeError:
        return {"score": 0, "queries": None}

    if not isinstance(queries, list):
        return {"score": score, "queries": queries}
    score += 2

    if len(queries) == 3:
        score += 2
    elif len(queries) > 0:
        score += 1

    if all(isinstance(q, str) for q in queries):
        score += 2

    if queries and all(len(q.strip()) > 15 for q in queries if isinstance(q, str)):
        score += 1

    return {"score": score, "queries": queries if isinstance(queries, list) else None}


# ── Model Grader (quality) ────────────────────────────────────────────────────

async def grade_quality(task: str, queries: list[str], solution_criteria: str) -> int:
    """Ask Claude to score query relevance + specificity + coverage (1–10)."""
    if not queries:
        return 0

    queries_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(queries))
    prompt = (
        f"You are evaluating search queries generated for an AI policy research task.\n\n"
        f"<task>\n{task}\n</task>\n\n"
        f"<criteria>\n{solution_criteria}\n</criteria>\n\n"
        f"<queries>\n{queries_text}\n</queries>\n\n"
        f"Score these queries 1-10 based on:\n"
        f"- Relevance: Do they directly address the research task?\n"
        f"- Specificity: Are they precise enough to find useful sources?\n"
        f"- Coverage: Together, do they cover all angles in the criteria?\n\n"
        f'Return JSON with keys: "score" (int 1-10), "reasoning" (string, ≤ 60 chars)'
    )
    try:
        raw = await generate_text(
            prompt, temperature=0.0, prefill="```json", stop_sequences=["```"]
        )
        json_str = raw[len("```json"):].strip()
        data = json.loads(json_str)
        # model sometimes wraps in an array — unwrap if needed
        if isinstance(data, list):
            data = data[0]
        return int(data.get("score", 0))
    except Exception as e:
        print(f"  ⚠️  grade_quality error: {e!r}")
        return 0


# ── Run One Version ───────────────────────────────────────────────────────────

async def run_version(version: dict, dataset: list[dict]) -> dict:
    """Run all test cases for one prompt version; return aggregated results."""
    code_scores, model_scores, combined_scores = [], [], []

    for case in dataset:
        raw = await generate_text(
            version["build"](case["task"]),
            temperature=0.2,
            prefill="```json",
            stop_sequences=["```"],
        )
        code = grade_structure(raw)
        model_score = 0
        if code["queries"]:
            model_score = await grade_quality(
                case["task"], code["queries"], case["solution_criteria"]
            )

        code_scores.append(code["score"])
        model_scores.append(model_score)
        combined_scores.append((code["score"] + model_score) / 2)

    return {
        "name": version["name"],
        "description": version["description"],
        "avg_code":     sum(code_scores)     / len(code_scores),
        "avg_model":    sum(model_scores)    / len(model_scores),
        "avg_combined": sum(combined_scores) / len(combined_scores),
        "pass_rate":    sum(1 for s in combined_scores if s >= 8),
        "n":            len(dataset),
    }


# ── Comparison Report ─────────────────────────────────────────────────────────

def print_comparison(results: list[dict]) -> None:
    """Print a version-over-version comparison table."""
    print(f"\n{'='*70}")
    print("  Prompt Engineering: Iterative Improvement Results")
    print(f"  Dataset: {results[0]['n']} test cases  |  Pass threshold: ≥ 8 combined")
    print(f"{'='*70}")
    print(f"  {'Version':<18} {'Code':>5} {'Model':>6} {'Combined':>9} {'Pass':>6}  Change")
    print(f"  {'-'*18} {'-'*5} {'-'*6} {'-'*9} {'-'*6}  {'-'*10}")

    prev_combined = None
    for r in results:
        delta = ""
        if prev_combined is not None:
            diff = r["avg_combined"] - prev_combined
            delta = f"  {'↑' if diff > 0 else '↓'} {abs(diff):+.1f}"
        print(
            f"  {r['name']:<18} "
            f"{r['avg_code']:>5.1f} "
            f"{r['avg_model']:>6.1f} "
            f"{r['avg_combined']:>9.1f} "
            f"{r['pass_rate']:>3}/{r['n']}"
            f"{delta}"
        )
        print(f"    └─ {r['description']}")
        prev_combined = r["avg_combined"]

    best = max(results, key=lambda r: r["avg_combined"])
    print(f"\n  🏆 Best version: {best['name']}  (combined {best['avg_combined']:.1f}/10)")
    print(f"{'='*70}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_comparison():
    print(f"\n{'='*70}")
    print("  Prompt Engineering: Running iterative improvement comparison")
    print(f"  Versions: {len(PROMPT_VERSIONS)}  |  Cases per version: {len(TEST_DATASET)}")
    print(f"{'='*70}")

    all_results = []
    for version in PROMPT_VERSIONS:
        print(f"\n▶ Evaluating {version['name']} ...")
        result = await run_version(version, TEST_DATASET)
        all_results.append(result)
        print(f"  Combined avg: {result['avg_combined']:.1f}/10  "
              f"(code {result['avg_code']:.1f}, model {result['avg_model']:.1f})")

    print_comparison(all_results)

    # HTML report — one row per version showing score progression
    best = max(all_results, key=lambda r: r["avg_combined"])
    entries = [
        {
            "scenario":   r["name"],
            "inputs":     {"technique": r["description"]},
            "criteria":   ["Valid JSON array of 3 queries", "High relevance + coverage"],
            "output":     (
                f"code={r['avg_code']:.1f}/10  "
                f"model={r['avg_model']:.1f}/10  "
                f"combined={r['avg_combined']:.1f}/10\n"
                f"pass={r['pass_rate']}/{r['n']}"
            ),
            "score":      r["avg_combined"],
            "reasoning":  (
                "🏆 Best version" if r["name"] == best["name"]
                else r["description"]
            ),
        }
        for r in all_results
    ]
    html = build_html_report(
        entries,
        title="Prompt Engineering: Iterative Improvement",
        pass_threshold=8.0,
    )
    save_report(html, "evals/reports/prompt_versions.html")

    return all_results


if __name__ == "__main__":
    asyncio.run(run_comparison())
