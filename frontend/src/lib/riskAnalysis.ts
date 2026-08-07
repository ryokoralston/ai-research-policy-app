/**
 * Shared display metadata for the Risk Analysis pages.
 *
 * Both /analysis and /analysis/[analysisId] render analysis-type badges and
 * risk-score bars, and previously kept their own copies of these maps — so
 * adding an analysis type or a risk dimension silently left one page showing
 * raw ids. They live here instead; the pages import them.
 */

/** Subject categories offered in the "New Analysis" form. `id` is sent to the
 *  backend as `analysis_type` and stored verbatim (it is not an enum there). */
export const ANALYSIS_TYPES: { id: string; label: string }[] = [
  { id: "technology", label: "Technology" },
  { id: "policy", label: "Policy" },
  { id: "actor", label: "Actor/Organization" },
  { id: "use_case", label: "Use Case / Sector" },
  { id: "supply_chain", label: "Supply Chain / Infrastructure" },
];

/** id → label, for rendering the type of an already-saved analysis. Derived so
 *  it cannot drift from ANALYSIS_TYPES. */
export const ANALYSIS_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  ANALYSIS_TYPES.map((t) => [t.id, t.label])
);

/** Risk-dimension key → label. Keys mirror RISK_DIMENSIONS in the backend's
 *  templates/risk_assessment.py; both pages fall back to the raw key when a
 *  dimension has no entry here, so a missing label degrades rather than breaks. */
export const SCORE_LABELS: Record<string, string> = {
  capability: "Capability",
  deployment: "Deployment Speed",
  governance: "Governance Gap",
  geopolitical: "Geopolitical Risk",
  misuse: "Misuse Potential",
  equity: "Rights & Equity",
  systemic: "Systemic Risk",
};
