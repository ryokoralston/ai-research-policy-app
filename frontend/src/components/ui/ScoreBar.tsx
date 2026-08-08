import { clsx } from "clsx";
import { SOURCE_TIER_BY_KEY } from "@/lib/riskAnalysis";

/** Bar geometry per call site. The analysis list shows the dimensions stacked
 *  in a narrow sidebar next to streaming text, so it uses the thinner "sm"
 *  bar; the detail page gives them a full-width two-column grid and uses the
 *  heavier "md" bar as the page's primary readout. */
const SIZES = {
  sm: { track: "h-1.5", value: "text-slate-100 font-mono", fill: "" },
  md: { track: "h-2", value: "text-slate-100 font-mono font-semibold", fill: "transition-all" },
} as const;

/** Matches WEAK_DIMENSION_THRESHOLD in backend/services/risk_analyzer.py.
 *  Below this the backend spends an extra research pass on the dimension, so
 *  it is the honest place to warn the reader. */
const WEAK_EVIDENCE_THRESHOLD = 6;

interface ScoreBarProps {
  label: string;
  /** 1-10 risk score: how much this dimension raises the risk. */
  score: number;
  /** 0-10 evidence support: how directly the retrieved source material backs
   *  what was written. NOT source authority and NOT a probability that the
   *  assessment is right — a dimension can be well supported by weak sources.
   *  Omitted when the analysis ran with no source material to grade against,
   *  in which case nothing is shown: absent is not the same as low. */
  evidenceSupport?: number;
  /** Provenance mix of the sources this dimension cited, {tier: count}.
   *  Counts, never a score — see services/analysis_sources.py. */
  citationTiers?: Record<string, number>;
  size?: keyof typeof SIZES;
}

export default function ScoreBar({
  label,
  score,
  evidenceSupport,
  citationTiers,
  size = "sm",
}: ScoreBarProps) {
  const color = score >= 7 ? "bg-red-500" : score >= 5 ? "bg-amber-500" : "bg-green-500";
  const s = SIZES[size];
  const weak = evidenceSupport !== undefined && evidenceSupport < WEAK_EVIDENCE_THRESHOLD;
  const mix = Object.entries(citationTiers || {});

  return (
    <div>
      <div className="flex justify-between items-baseline text-xs mb-1 gap-2">
        <span className="text-slate-400 truncate">{label}</span>
        <span className="flex items-baseline gap-1.5 shrink-0">
          {evidenceSupport !== undefined && (
            <span
              className={clsx("text-[10px] font-mono", weak ? "text-amber-400" : "text-slate-500")}
              title={
                "Evidence support: how directly the retrieved sources back this dimension. " +
                "Not source authority, and not a probability that the assessment is correct."
              }
            >
              ev {evidenceSupport}
            </span>
          )}
          <span className={s.value}>{score}/10</span>
        </span>
      </div>
      <div className={clsx("bg-slate-700 rounded-full overflow-hidden", s.track)}>
        <div
          className={clsx("h-full rounded-full", color, s.fill)}
          style={{ width: `${score * 10}%` }}
        />
      </div>
      {mix.length > 0 && (
        <div className="flex flex-wrap gap-x-2 gap-y-0.5 mt-1.5 text-[10px] text-slate-600">
          {mix.map(([tier, n]) => {
            const t = SOURCE_TIER_BY_KEY[tier] ?? SOURCE_TIER_BY_KEY.unknown;
            return (
              <span key={tier} title={`${n} cited source(s): ${t.label}`}>
                <span className="text-slate-500">{n}</span> {t.short.toLowerCase()}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
