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

/** Publisher provenance tiers, mirroring TIERS in the backend's
 *  services/source_tier.py — same keys, same order, most authoritative first.
 *
 *  These describe WHO published a source, not whether it is correct: a vendor
 *  blog can be accurate and a peer-reviewed paper can be wrong. They are shown
 *  so a reader can see what an assessment is built on; nothing in the app
 *  weights or scores a claim by tier. "unknown" means unclassified — including
 *  every source gathered before tiering existed — and is not a low tier. */
export const SOURCE_TIERS: { key: string; label: string; short: string; className: string }[] = [
  { key: "official", label: "Official / Regulator", short: "Official", className: "bg-blue-900/50 text-blue-300" },
  { key: "peer_reviewed", label: "Peer-reviewed", short: "Peer-rev.", className: "bg-green-900/50 text-green-300" },
  { key: "journalism", label: "Research & Journalism", short: "Research", className: "bg-slate-700 text-slate-300" },
  { key: "advocacy", label: "Advocacy", short: "Advocacy", className: "bg-amber-900/50 text-amber-300" },
  { key: "vendor", label: "Vendor / Commercial", short: "Vendor", className: "bg-red-900/50 text-red-300" },
  { key: "unknown", label: "Unclassified", short: "Unclassified", className: "bg-slate-800 text-slate-500" },
];

export const SOURCE_TIER_BY_KEY: Record<string, (typeof SOURCE_TIERS)[number]> =
  Object.fromEntries(SOURCE_TIERS.map((t) => [t.key, t]));

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
