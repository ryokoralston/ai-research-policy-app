import type { ConsensusClaim } from "@/lib/types";

/** The subset of PersonaMeta (see app/debate/page.tsx's PERSONA_MAP) this component reads. */
interface PersonaMetaLite {
  name: string;
  initials: string;
  color: string; // Tailwind bg-* class, same convention as PERSONA_MAP/exportDebate.ts
}

/**
 * Compact "Consensus Meter" for the Multi-Persona Debate feature: one row
 * per contested claim (from backend services/consensus_meter.py), showing
 * every persona's real stance as a small colored badge — green ring =
 * agree, red ring = disagree, amber ring = mixed (same green/amber/red
 * scheme CitationConfidenceCard.tsx already uses elsewhere in this app).
 * Renders nothing if there are no claims — same graceful-absence pattern
 * used for other optional post-generation data in this app.
 */
export default function ConsensusMeter({
  claims,
  personaMap,
}: {
  claims: ConsensusClaim[];
  personaMap: Record<string, PersonaMetaLite>;
}) {
  if (!claims || claims.length === 0) return null;

  const RING: Record<string, string> = {
    agree: "ring-green-500",
    disagree: "ring-red-500",
    mixed: "ring-amber-500",
  };
  const LABEL: Record<string, string> = {
    agree: "Agree",
    disagree: "Disagree",
    mixed: "Mixed / not addressed",
  };

  return (
    <section>
      <div className="flex items-center gap-3 mb-4">
        <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-indigo-900/50 text-indigo-300">
          Consensus Meter
        </span>
        <h2 className="text-sm font-semibold text-slate-300">Where Participants Agreed &amp; Diverged</h2>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4">
        {/* Legend */}
        <div className="flex items-center gap-4 text-xs text-slate-500 pb-3 border-b border-slate-800">
          {(["agree", "disagree", "mixed"] as const).map((stance) => (
            <span key={stance} className="flex items-center gap-1.5">
              <span className={`w-3 h-3 rounded-full ring-2 ring-offset-1 ring-offset-slate-900 bg-slate-700 ${RING[stance]}`} />
              {LABEL[stance]}
            </span>
          ))}
        </div>

        {/* Claims */}
        <div className="space-y-3">
          {claims.map((claim, i) => (
            <div key={i} className="flex items-center justify-between gap-4 flex-wrap">
              <p className="text-slate-300 text-sm flex-1 min-w-[200px]">{claim.claim}</p>
              <div className="flex items-center gap-1.5 flex-wrap">
                {Object.entries(claim.stances).map(([personaKey, stance]) => {
                  const meta = personaMap[personaKey];
                  const ring = RING[stance] ?? "ring-slate-600";
                  return (
                    <span
                      key={personaKey}
                      title={`${meta?.name ?? personaKey}: ${LABEL[stance] ?? stance}`}
                      className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-white flex-shrink-0 ring-2 ring-offset-1 ring-offset-slate-900 ${meta?.color ?? "bg-slate-700"} ${ring}`}
                    >
                      {meta?.initials ?? "??"}
                    </span>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
