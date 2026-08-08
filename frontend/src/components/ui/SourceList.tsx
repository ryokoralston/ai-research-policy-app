import { ExternalLink } from "lucide-react";
import type { SourceRef } from "@/lib/types";
import { SOURCE_TIERS, SOURCE_TIER_BY_KEY } from "@/lib/riskAnalysis";

/** Resolves the [Source N] markers used throughout an assessment.
 *
 *  The assessment text cites sources by number and nothing else, so without
 *  this list every citation in the document is unverifiable. The number shown
 *  here is the citation key from the text — it is rendered explicitly rather
 *  than via an <ol>, so it can never be renumbered by the browser.
 *
 *  Each source carries its publisher provenance. That is deliberately a label
 *  and not a score: it tells the reader a cost figure came from a vendor and a
 *  fine rate from a campaigning group, without pretending to know which is
 *  right. */
function TierBadge({ tier }: { tier?: string }) {
  const t = SOURCE_TIER_BY_KEY[tier || "unknown"] ?? SOURCE_TIER_BY_KEY.unknown;
  return (
    <span
      title={`Publisher: ${t.label}`}
      className={`inline-flex shrink-0 items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${t.className}`}
    >
      {t.short}
    </span>
  );
}

/** One line reading of what the whole assessment rests on. */
function ProvenanceBreakdown({ sources }: { sources: SourceRef[] }) {
  const counts = SOURCE_TIERS.map((t) => ({
    ...t,
    n: sources.filter((s) => (s.tier || "unknown") === t.key).length,
  })).filter((t) => t.n > 0);

  if (counts.length <= 1) return null;

  return (
    <p className="text-xs text-slate-500 mb-4">
      {counts.map((t, i) => (
        <span key={t.key}>
          {i > 0 && <span className="text-slate-700"> · </span>}
          <span className="text-slate-400">{t.n}</span> {t.label.toLowerCase()}
        </span>
      ))}
    </p>
  );
}

export default function SourceList({ sources }: { sources: SourceRef[] }) {
  if (!sources.length) return null;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 mt-6">
      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
        Sources ({sources.length})
      </h2>
      <ProvenanceBreakdown sources={sources} />
      <ul className="space-y-2.5">
        {sources.map((s) => (
          <li key={s.order} className="flex gap-3 text-sm">
            <span className="text-slate-500 font-mono text-xs shrink-0 pt-0.5 w-6 text-right">
              {s.order}
            </span>
            <a
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              className="group min-w-0 flex-1"
            >
              <span className="flex items-start gap-2">
                <span className="text-slate-200 group-hover:text-blue-400 transition-colors">
                  {s.title}
                  <ExternalLink
                    size={11}
                    className="inline ml-1.5 mb-0.5 text-slate-600 group-hover:text-blue-400 transition-colors"
                  />
                </span>
                <TierBadge tier={s.tier} />
              </span>
              <span className="block text-slate-600 text-xs truncate">{s.url}</span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
