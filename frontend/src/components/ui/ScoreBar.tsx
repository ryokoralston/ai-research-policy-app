import { clsx } from "clsx";

/** Bar geometry per call site. The analysis list shows six or seven of these
 *  stacked in a narrow sidebar next to streaming text, so it uses the thinner
 *  "sm" bar; the detail page gives them a full-width two-column grid and uses
 *  the heavier "md" bar as the page's primary readout. */
const SIZES = {
  sm: { track: "h-1.5", value: "text-slate-100 font-mono", fill: "" },
  md: { track: "h-2", value: "text-slate-100 font-mono font-semibold", fill: "transition-all" },
} as const;

interface ScoreBarProps {
  label: string;
  /** 1-10, as produced by the backend's risk-dimension scoring. */
  score: number;
  size?: keyof typeof SIZES;
}

export default function ScoreBar({ label, score, size = "sm" }: ScoreBarProps) {
  const color = score >= 7 ? "bg-red-500" : score >= 5 ? "bg-amber-500" : "bg-green-500";
  const s = SIZES[size];
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-slate-400">{label}</span>
        <span className={s.value}>{score}/10</span>
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
