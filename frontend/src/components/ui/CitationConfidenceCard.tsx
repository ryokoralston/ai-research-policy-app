import type { CitationConfidence } from "@/lib/types";

/**
 * Small card showing the grounding-verification confidence score and any
 * unsupported claims (see backend services/citation_verifier.py). Renders
 * nothing if `confidence` is null/undefined — verification wasn't run,
 * failed, or there was no source material to check against.
 */
export default function CitationConfidenceCard({
  confidence,
}: {
  confidence: CitationConfidence | null | undefined;
}) {
  if (!confidence) return null;

  const score = confidence.confidence_score;
  const claims = confidence.unsupported_claims || [];
  const color =
    score == null ? "text-slate-100" : score >= 7 ? "text-green-400" : score >= 4 ? "text-amber-400" : "text-red-400";

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 mb-6">
      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
        Citation Confidence
      </h2>
      <div className="flex items-baseline gap-3 mb-2">
        <span className={`text-2xl font-bold font-mono ${color}`}>
          {score != null ? `${score}/10` : "—"}
        </span>
        {confidence.notes && <span className="text-slate-500 text-xs">{confidence.notes}</span>}
      </div>
      {claims.length > 0 && (
        <div className="mt-3 bg-amber-900/20 border border-amber-800/50 rounded-lg p-3">
          <p className="text-amber-300 text-xs font-medium mb-1.5">
            Unsupported claims detected:
          </p>
          <ul className="list-disc list-inside space-y-1">
            {claims.map((claim, i) => (
              <li key={i} className="text-amber-200/80 text-xs">
                {claim}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
