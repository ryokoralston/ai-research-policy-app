import { ExternalLink } from "lucide-react";
import type { SourceRef } from "@/lib/types";

/** Resolves the [Source N] markers used throughout an assessment.
 *
 *  The assessment text cites sources by number and nothing else, so without
 *  this list every citation in the document is unverifiable. The number shown
 *  here is the citation key from the text — it is rendered explicitly rather
 *  than via an <ol>, so it can never be renumbered by the browser. */
export default function SourceList({ sources }: { sources: SourceRef[] }) {
  if (!sources.length) return null;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 mt-6">
      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">
        Sources ({sources.length})
      </h2>
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
              <span className="text-slate-200 group-hover:text-blue-400 transition-colors">
                {s.title}
                <ExternalLink
                  size={11}
                  className="inline ml-1.5 mb-0.5 text-slate-600 group-hover:text-blue-400 transition-colors"
                />
              </span>
              <span className="block text-slate-600 text-xs truncate">{s.url}</span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
