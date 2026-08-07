import { clsx } from "clsx";

/** Bar geometry per call site. The analysis list shows the dimensions stacked
 *  in a narrow sidebar next to streaming text, so it uses the thinner "sm"
 *  bar; the detail page gives them a full-width two-column grid and uses the
 *  heavier "md" bar as the page's primary readout. */
const SIZES = {
  sm: { track: "h-1.5", value: "text-slate-100 font-mono", fill: "" },
  md: { track: "h-2", value: "text-slate-100 font-mono font-semibold", fill: "transition-all" },
} as const;

/** Matches WEAK_DIMENSION_THRESHOLD in backend/services/risk_analyzer.py.
 *  Below this the backend considers a dimension weakly grounded and spends an
 *  extra research pass on it, so it is the honest place to warn the reader. */
const WEAK_CONFIDENCE_THRESHOLD = 6;

interface ScoreBarProps {
  label: string;
  /** 1-10 risk score: how bad this dimension looks. */
  score: number;
  /** 0-10 grounding confidence: how well-evidenced that judgement is. Omitted
   *  when the analysis ran without source material to grade against, in which
   *  case nothing is shown — absent is not the same as low. */
  confidence?: number;
  size?: keyof typeof SIZES;
}

export default function ScoreBar({ label, score, confidence, size = "sm" }: ScoreBarProps) {
  const color = score >= 7 ? "bg-red-500" : score >= 5 ? "bg-amber-500" : "bg-green-500";
  const s = SIZES[size];
  const weak = confidence !== undefined && confidence < WEAK_CONFIDENCE_THRESHOLD;
  return (
    <div>
      <div className="flex justify-between items-baseline text-xs mb-1 gap-2">
        <span className="text-slate-400 truncate">{label}</span>
        <span className="flex items-baseline gap-1.5 shrink-0">
          {confidence !== undefined && (
            <span
              className={clsx("text-[10px] font-mono", weak ? "text-amber-400" : "text-slate-500")}
              title={
                weak
                  ? "Grounding confidence is low — this dimension rests on thin evidence"
                  : "Grounding confidence: how well the sources support this dimension"
              }
            >
              ev {confidence}
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
    </div>
  );
}
