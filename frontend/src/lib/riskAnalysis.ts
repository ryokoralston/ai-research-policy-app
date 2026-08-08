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

/** Risk-dimension key → label, for every key in every dimension set (see
 *  DIMENSION_SETS in the backend's templates/risk_assessment.py). Which subset
 *  an analysis actually has depends on its subject type, so this is the union,
 *  not one set. Both pages fall back to the raw key when a dimension has no
 *  entry here, so a missing label degrades rather than breaks. */
export const SCORE_LABELS: Record<string, string> = {
  // System set — technology, use case, supply chain
  capability: "Capability",
  deployment: "Deployment Speed",
  governance: "Governance Gap",
  misuse: "Misuse Potential",
  // Instrument set — policy
  enforcement: "Enforcement Gap",
  uncertainty: "Legal Uncertainty",
  fragmentation: "Fragmentation",
  burden: "Compliance Burden",
  // Organization set — actor
  governance_maturity: "Governance Maturity",
  accountability: "Accountability",
  concentration: "Concentration",
  // Shared across sets
  geopolitical: "Geopolitical Risk",
  equity: "Rights & Equity",
  systemic: "Systemic Risk",
};
