"""
Shared HTML Report Generator
============================
Adapted from the Anthropic Skilljar notebook (002_prompting_completed.ipynb).
Generates a colour-coded HTML evaluation report for any eval in this package.

Usage:
    from evals.report import build_html_report, save_report

    entries = [
        {
            "scenario":   "What are AI governance risks?",
            "inputs":     {"query": "AI governance risks"},
            "criteria":   ["Cites sources", "Has policy recommendations"],
            "output":     "The synthesis text...",
            "score":      8.5,
            "reasoning":  "Strong citations but thin recommendations.",
        },
        ...
    ]
    html = build_html_report(entries, title="Synthesis Eval", pass_threshold=7)
    save_report(html, "reports/synthesis_eval.html")
"""

from pathlib import Path
from statistics import mean


def build_html_report(
    entries: list[dict],
    title: str = "Prompt Evaluation Report",
    pass_threshold: float = 7.0,
) -> str:
    """
    Build an HTML report from a list of evaluation entries.

    Each entry dict must have:
        scenario   : str          — what is being tested
        inputs     : dict         — prompt inputs shown in the table
        criteria   : list[str]    — solution criteria
        output     : str          — model output (truncated for display)
        score      : float        — 0-10
        reasoning  : str          — grader reasoning

    Optional:
        extra_info : dict         — extra key-value rows (e.g. per-dimension scores)
    """
    if not entries:
        return "<html><body><p>No results.</p></body></html>"

    scores = [e["score"] for e in entries]
    avg_score = mean(scores)
    pass_rate = 100 * len([s for s in scores if s >= pass_threshold]) / len(scores)

    rows_html = ""
    for e in entries:
        inputs_html = "<br>".join(
            f"<strong>{k}:</strong> {v}" for k, v in (e.get("inputs") or {}).items()
        )
        criteria_html = "<br>• ".join(e.get("criteria") or [])
        if criteria_html:
            criteria_html = "• " + criteria_html

        # Extra info rows (e.g. per-dimension scores for synthesis eval)
        extra_html = ""
        for k, v in (e.get("extra_info") or {}).items():
            extra_html += f"<br><strong>{k}:</strong> {v}"

        score = e["score"]
        if score >= 8:
            score_class = "score-high"
        elif score <= 5:
            score_class = "score-low"
        else:
            score_class = "score-medium"

        # Truncate long output for readability
        output_text = str(e.get("output", ""))
        if len(output_text) > 600:
            output_text = output_text[:600] + "\n…(truncated)"

        rows_html += f"""
        <tr>
            <td>{e.get('scenario', '')}{extra_html}</td>
            <td class="prompt-inputs">{inputs_html}</td>
            <td class="criteria">{criteria_html}</td>
            <td class="output"><pre>{output_text}</pre></td>
            <td class="score-col"><span class="score {score_class}">{score:.1f}</span></td>
            <td class="reasoning">{e.get('reasoning', '')}</td>
        </tr>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; line-height: 1.6; margin: 0; padding: 20px; color: #333; }}
    .header {{ background: #f0f0f0; padding: 20px; border-radius: 5px; margin-bottom: 20px; }}
    .summary-stats {{ display: flex; flex-wrap: wrap; gap: 10px; }}
    .stat-box {{ background: #fff; border-radius: 5px; padding: 15px;
                 box-shadow: 0 2px 5px rgba(0,0,0,.1); flex-basis: 30%; min-width: 180px; }}
    .stat-value {{ font-size: 24px; font-weight: bold; margin-top: 5px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
    th {{ background: #4a4a4a; color: #fff; text-align: left; padding: 12px; }}
    td {{ padding: 10px; border-bottom: 1px solid #ddd; vertical-align: top; width: 20%; }}
    tr:nth-child(even) {{ background: #f9f9f9; }}
    .score {{ font-weight: bold; padding: 5px 10px; border-radius: 3px; display: inline-block; }}
    .score-high   {{ background: #c8e6c9; color: #2e7d32; }}
    .score-medium {{ background: #fff9c4; color: #f57f17; }}
    .score-low    {{ background: #ffcdd2; color: #c62828; }}
    .score-col {{ width: 70px; }}
    .output pre {{ background: #f5f5f5; border: 1px solid #ddd; border-radius: 4px;
                   padding: 10px; margin: 0; font-family: monospace; font-size: 13px;
                   white-space: pre-wrap; word-wrap: break-word; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>{title}</h1>
    <div class="summary-stats">
      <div class="stat-box">
        <div>Total Test Cases</div>
        <div class="stat-value">{len(entries)}</div>
      </div>
      <div class="stat-box">
        <div>Average Score</div>
        <div class="stat-value">{avg_score:.1f} / 10</div>
      </div>
      <div class="stat-box">
        <div>Pass Rate (≥{pass_threshold:.0f})</div>
        <div class="stat-value">{pass_rate:.1f}%</div>
      </div>
    </div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Scenario</th>
        <th>Prompt Inputs</th>
        <th>Solution Criteria</th>
        <th>Output</th>
        <th>Score</th>
        <th>Reasoning</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
</body>
</html>"""


def save_report(html: str, path: str) -> None:
    """Write the HTML report to disk, creating parent dirs if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    print(f"  📄 HTML report saved → {p}")
