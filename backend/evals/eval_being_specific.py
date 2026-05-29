"""
Being Specific: Output Guidelines + Process Steps
=================================================
Anthropic Academy lesson "Being Specific" applied to this app.

The lesson teaches two ways to be specific in a prompt:

  1. Output Quality Guidelines — a list of qualities the output must have
       (length, structure, elements to include, tone). USE ALMOST ALWAYS.
  2. Process Steps — numbered steps for Claude to think through before
       answering. USE FOR COMPLEX / MULTI-ANGLE PROBLEMS.

This eval demonstrates both techniques on a REAL app prompt: the per-source
summarization step in research_agent.py. We run three versions against the
SAME dataset so scores are directly comparable (same pattern as
eval_prompt_versions.py):

  v1 → naive ("summarize this")            — establishes a low baseline
  v2 → + Output Quality Guidelines         — THE LIVE production prompt
  v3 → + Process Steps                     — think-then-write (tested, not adopted)

v2 and v3 both call the real production builder, so this eval doubles as a
regression guard on the live prompt. In the lesson's meal-planning example,
adding guidelines lifted the score from 3.92 to 7.86 — this eval reproduces
that "specificity lifts score" effect (v1 → v2). It also tests the lesson's
nuance that Process Steps help COMPLEX problems: on simple single-source
summarization (v2 → v3) they add no measurable gain, so production keeps
guidelines only.

Usage:
  cd backend
  source venv/bin/activate
  python -m evals.eval_being_specific
"""

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.anthropic_client import generate_text
from services.research_agent import build_source_summary_prompt
from evals.report import build_html_report, save_report

# ── Shared Dataset ────────────────────────────────────────────────────────────
# Realistic AI-policy web-source excerpts. Same cases for every version →
# scores are directly comparable. solution_criteria describes what a good
# summary must capture (used by the model grader).

TEST_DATASET = [
    {
        "query": "How is the EU AI Act regulating general-purpose AI models?",
        "title": "EU AI Act: General-Purpose AI Obligations Take Effect",
        "url": "https://example.org/eu-ai-act-gpai",
        "content": (
            "As of August 2025, providers of general-purpose AI (GPAI) models "
            "placed on the EU market must comply with new transparency duties, "
            "including publishing summaries of training data and maintaining "
            "technical documentation. Models deemed to pose 'systemic risk' "
            "(trained using more than 10^25 FLOPs) face additional obligations: "
            "adversarial testing, incident reporting, and cybersecurity measures. "
            "The AI Office will enforce these rules, with fines up to 3% of global "
            "turnover. Industry groups argue the FLOP threshold is arbitrary and "
            "may capture models that pose little real-world risk, while some "
            "researchers counter that compute is a weak proxy for capability."
        ),
        "solution_criteria": (
            "A good summary captures the August 2025 effective date, the GPAI "
            "transparency duties, the 10^25 FLOP systemic-risk threshold and its "
            "extra obligations, the 3% turnover fine, and notes the contested "
            "nature of the compute threshold."
        ),
    },
    {
        "query": "What US federal guidance exists for managing generative AI risks?",
        "title": "NIST Releases Generative AI Profile for the AI Risk Management Framework",
        "url": "https://example.org/nist-genai-profile",
        "content": (
            "NIST published a Generative AI Profile as a companion to its AI Risk "
            "Management Framework (AI RMF). The profile identifies 12 risks unique "
            "to or amplified by generative AI, including confabulation (hallucination), "
            "dangerous content generation, data privacy leakage, and homogenization "
            "of outputs. For each risk it suggests voluntary actions across the AI "
            "lifecycle — govern, map, measure, manage. The document is explicitly "
            "non-binding guidance, not regulation, and NIST notes it reflects current "
            "consensus that may evolve as the technology matures."
        ),
        "solution_criteria": (
            "A good summary captures that this is a voluntary, non-binding companion "
            "to the AI RMF, names a few of the 12 generative-AI risks (e.g. "
            "confabulation, data leakage), references the govern/map/measure/manage "
            "structure, and flags that it is guidance rather than regulation."
        ),
    },
    {
        "query": "Are voluntary AI safety commitments by leading labs effective?",
        "title": "Op-Ed: Why Voluntary AI Safety Commitments Are Not Enough",
        "url": "https://example.org/voluntary-commitments-oped",
        "content": (
            "In this opinion piece, a former regulator argues that the voluntary "
            "safety commitments made by leading AI labs in 2023 have produced little "
            "verifiable change. The author claims that without independent auditing, "
            "self-reported red-teaming results cannot be trusted, and points to the "
            "absence of any penalty for non-compliance. The piece offers no new data, "
            "relying instead on the author's interpretation of public statements, and "
            "calls for binding legislation modeled on financial-sector oversight."
        ),
        "solution_criteria": (
            "A good summary makes clear this is opinion, not evidence; captures the "
            "core argument (voluntary commitments lack auditing and penalties); notes "
            "the proposed remedy (binding legislation like financial oversight); and "
            "explicitly flags the lack of new data as a limitation."
        ),
    },
    {
        "query": "What oversight exists for AI clinical decision tools in US hospitals?",
        "title": "Study: Adoption of AI Decision Tools in US Hospitals Outpaces Oversight",
        "url": "https://example.org/ai-hospitals-study",
        "content": (
            "A peer-reviewed study surveying 312 US hospitals found that 64% had "
            "deployed at least one AI clinical decision-support tool by 2024, but only "
            "29% had a formal validation process and just 11% monitored the tools for "
            "performance drift after deployment. The authors report that smaller, rural "
            "hospitals were least likely to have oversight in place. They caution that "
            "the survey relied on self-reported administrator responses and a 41% "
            "response rate, which may bias results toward better-resourced institutions."
        ),
        "solution_criteria": (
            "A good summary captures the key figures (64% adoption, 29% validation, "
            "11% drift monitoring), the rural-hospital gap, identifies this as a "
            "peer-reviewed survey, and notes the stated limitations (self-reported "
            "data, 41% response rate, possible bias)."
        ),
    },
]


# ── Prompt Versions ───────────────────────────────────────────────────────────
# Technique progression mirrors the "Being Specific" lesson:
#   v1 → naive (no guidelines at all)
#   v2 → + Output Quality Guidelines (length, structure, elements, tone)
#   v3 → + Process Steps (think-then-write for reliability on a complex task)

# v2 and v3 both call the REAL production builder (no duplicated copy that can
# drift). v2 = production (Output Guidelines). v3 = production + Process Steps.
# So the only difference between v2 and v3 is the second technique → a clean
# A/B test of whether Process Steps help on this task.

PROMPT_VERSIONS = [
    {
        "name": "v1 (naive)",
        "description": "Bare minimum — no length, structure, or quality guidelines",
        "build": lambda s: (
            f"Summarize this source about AI policy.\n\n"
            f"Title: {s['title']}\n"
            f"Content:\n{s['content']}"
        ),
    },
    {
        "name": "v2 (production)",
        "description": "Output Quality Guidelines — the live research_agent.py prompt",
        "build": lambda s: build_source_summary_prompt(
            query=s["query"], title=s["title"], url=s["url"], content=s["content"],
        ),
    },
    {
        "name": "v3 (+process steps)",
        "description": "Production prompt + Process Steps (think-then-write) — tested, not adopted",
        "build": lambda s: build_source_summary_prompt(
            query=s["query"], title=s["title"], url=s["url"], content=s["content"],
            include_process_steps=True,
        ),
    },
]


# ── Code Grader (structural) ──────────────────────────────────────────────────
# Rewards outputs that actually follow the guidelines. A naive prompt (v1)
# tends to miss bullets / limitations / length, so it scores lower here —
# which is exactly the "specificity produces compliant output" point.

_BULLET_RE = re.compile(r"(^|\n)\s*([-*•]|\d+\.)\s+")
# The guideline asks for a sentence on relevance to the query. Match the
# common faithful phrasings, not just the literal word "relevance" — e.g.
# "this source addresses the query", "informs the research", "useful for".
_RELEVANCE_RE = re.compile(
    r"relevan|pertinen|addresses (the|this)|informs the|bears on|"
    r"useful (for|to)|speaks to|contributes to|directly (addresses|relevant)",
    re.IGNORECASE,
)
_LIMITATION_RE = re.compile(
    r"(limitation|bias|biased|opinion|unsupported|caveat|gap|self-report|"
    r"response rate|non-binding|voluntary|no new data)",
    re.IGNORECASE,
)


def grade_structure(output: str) -> dict:
    """
    Rubric (total = 10):
      3 pts — has 2+ bullet points (key claims listed)
      3 pts — mentions a limitation / bias / opinion-vs-evidence
      2 pts — length in 60-180 words (partial: 1 pt if 40-220)
      2 pts — mentions relevance to research
    """
    score = 0
    reasons = []

    bullets = len(_BULLET_RE.findall(output))
    if bullets >= 2:
        score += 3
        reasons.append(f"✅ {bullets} bullet points  (+3)")
    else:
        reasons.append(f"⚠️  {bullets} bullet points (want ≥2)  (+0)")

    if _LIMITATION_RE.search(output):
        score += 3
        reasons.append("✅ Notes a limitation / bias  (+3)")
    else:
        reasons.append("⚠️  No limitation noted  (+0)")

    words = len(output.split())
    if 60 <= words <= 180:
        score += 2
        reasons.append(f"✅ Length {words}w in range  (+2)")
    elif 40 <= words <= 220:
        score += 1
        reasons.append(f"⚠️  Length {words}w near range  (+1)")
    else:
        reasons.append(f"❌ Length {words}w out of range  (+0)")

    if _RELEVANCE_RE.search(output):
        score += 2
        reasons.append("✅ Mentions relevance  (+2)")
    else:
        reasons.append("⚠️  No relevance statement  (+0)")

    return {"score": score, "reasons": reasons}


# ── Model Grader (quality) ────────────────────────────────────────────────────

async def grade_quality(source: dict, summary: str) -> dict:
    """Ask Claude to score the summary's faithfulness + specificity + usefulness."""
    prompt = (
        f"You are evaluating a summary written for AI policy research.\n\n"
        f"<source>\n{source['content']}\n</source>\n\n"
        f"<criteria>\n{source['solution_criteria']}\n</criteria>\n\n"
        f"<summary>\n{summary}\n</summary>\n\n"
        f"Score the summary 1-10 based on:\n"
        f"- Faithfulness: accurate to the source, no invented facts.\n"
        f"- Specificity: keeps the concrete figures, dates, and named actors.\n"
        f"- Critical read: distinguishes evidence from opinion and notes limitations.\n"
        f"- Usefulness: would help a policy researcher decide whether to read the source.\n\n"
        f'Return JSON with keys: "score" (int 1-10), "reasoning" (string, ≤ 80 chars)'
    )
    try:
        raw = await generate_text(
            prompt, temperature=0.0, prefill="```json", stop_sequences=["```"]
        )
        json_str = raw[len("```json"):].strip()
        data = json.loads(json_str)
        # model sometimes wraps the object in an array — unwrap if needed
        if isinstance(data, list):
            data = data[0]
        return {"score": int(data.get("score", 0)),
                "reasoning": str(data.get("reasoning", ""))}
    except Exception as e:
        print(f"  ⚠️  grade_quality error: {e!r}")
        return {"score": 0, "reasoning": f"grader error: {e}"}


# ── Run One Version ───────────────────────────────────────────────────────────

async def run_version(version: dict, dataset: list[dict]) -> dict:
    """Run all cases for one version; return aggregated scores + sample output."""
    code_scores, model_scores, combined_scores = [], [], []
    sample_output = ""
    sample_reasoning = ""

    for i, source in enumerate(dataset):
        # temperature=0.3 matches the real summary call in research_agent.py
        summary = await generate_text(version["build"](source), temperature=0.3)

        code = grade_structure(summary)
        model = await grade_quality(source, summary)

        code_scores.append(code["score"])
        model_scores.append(model["score"])
        combined_scores.append((code["score"] + model["score"]) / 2)

        if i == 0:  # keep first case as a sample for the HTML report
            sample_output = summary
            sample_reasoning = model["reasoning"]

    n = len(dataset)
    return {
        "name": version["name"],
        "description": version["description"],
        "avg_code":     sum(code_scores)     / n,
        "avg_model":    sum(model_scores)    / n,
        "avg_combined": sum(combined_scores) / n,
        "pass_rate":    sum(1 for s in combined_scores if s >= 8),
        "n":            n,
        "sample_output":    sample_output,
        "sample_reasoning": sample_reasoning,
    }


# ── Comparison Report ─────────────────────────────────────────────────────────

def print_comparison(results: list[dict]) -> None:
    print(f"\n{'='*72}")
    print("  Being Specific: Output Guidelines + Process Steps")
    print(f"  Dataset: {results[0]['n']} sources  |  Pass threshold: ≥ 8 combined")
    print(f"{'='*72}")
    print(f"  {'Version':<24} {'Code':>5} {'Model':>6} {'Combined':>9} {'Pass':>6}  Change")
    print(f"  {'-'*24} {'-'*5} {'-'*6} {'-'*9} {'-'*6}  {'-'*10}")

    prev = None
    for r in results:
        delta = ""
        if prev is not None:
            diff = r["avg_combined"] - prev
            delta = f"  {'↑' if diff > 0 else '↓'} {diff:+.1f}"
        print(
            f"  {r['name']:<24} "
            f"{r['avg_code']:>5.1f} "
            f"{r['avg_model']:>6.1f} "
            f"{r['avg_combined']:>9.1f} "
            f"{r['pass_rate']:>3}/{r['n']}"
            f"{delta}"
        )
        print(f"    └─ {r['description']}")
        prev = r["avg_combined"]

    best = max(results, key=lambda r: r["avg_combined"])
    print(f"\n  🏆 Best version: {best['name']}  (combined {best['avg_combined']:.1f}/10)")
    print(f"{'='*72}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_comparison():
    print(f"\n{'='*72}")
    print("  Being Specific: running output-guidelines + process-steps comparison")
    print(f"  Versions: {len(PROMPT_VERSIONS)}  |  Cases per version: {len(TEST_DATASET)}")
    print(f"{'='*72}")

    results = []
    for version in PROMPT_VERSIONS:
        print(f"\n▶ Evaluating {version['name']} ...")
        result = await run_version(version, TEST_DATASET)
        results.append(result)
        print(f"  Combined avg: {result['avg_combined']:.1f}/10  "
              f"(code {result['avg_code']:.1f}, model {result['avg_model']:.1f})")

    print_comparison(results)

    # HTML report — one row per version, showing a sample summary
    best = max(results, key=lambda r: r["avg_combined"])
    entries = [
        {
            "scenario":   r["name"],
            "inputs":     {"technique": r["description"]},
            "criteria":   ["Follows output guidelines (length/bullets/limitation)",
                           "High faithfulness + specificity (model grader)"],
            "output":     r["sample_output"],
            "score":      r["avg_combined"],
            "reasoning":  (
                "🏆 Best version. " if r["name"] == best["name"] else ""
            ) + r["sample_reasoning"],
            "extra_info": {
                "code":  f"{r['avg_code']:.1f}/10",
                "model": f"{r['avg_model']:.1f}/10",
                "pass":  f"{r['pass_rate']}/{r['n']}",
            },
        }
        for r in results
    ]
    html = build_html_report(
        entries,
        title="Being Specific: Output Guidelines + Process Steps",
        pass_threshold=8.0,
    )
    save_report(html, "evals/reports/being_specific.html")

    return results


if __name__ == "__main__":
    asyncio.run(run_comparison())
