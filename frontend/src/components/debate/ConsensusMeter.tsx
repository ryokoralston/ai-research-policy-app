import { Check, X, Minus } from "lucide-react";
import type { ConsensusClaim } from "@/lib/types";

/** The subset of PersonaMeta (see app/debate/page.tsx's PERSONA_MAP) this component reads. */
interface PersonaMetaLite {
  name: string;
  initials: string;
  color: string; // Tailwind bg-* class, same convention as PERSONA_MAP/exportDebate.ts
}

type Stance = "agree" | "disagree" | "mixed";

const STANCE_BADGE: Record<Stance, { bg: string; Icon: typeof Check }> = {
  agree: { bg: "bg-green-500", Icon: Check },
  disagree: { bg: "bg-red-500", Icon: X },
  mixed: { bg: "bg-amber-500", Icon: Minus },
};
const LABEL: Record<Stance, string> = {
  agree: "Agree",
  disagree: "Disagree",
  mixed: "Mixed / not addressed",
};

/**
 * Small solid-colored circle + icon (check/x/minus) — deliberately NOT a
 * colored ring around the persona avatar. An earlier version used a
 * stance-colored ring outline on top of the persona's own colored avatar,
 * which was hard to read: two different colors sharing one small circle
 * blended together, especially for personas whose own avatar color was
 * close in hue to the agree/disagree/mixed color. Icon shape now carries
 * the stance, color is just reinforcement — so it's legible even for
 * persona colors that are themselves green/red/amber.
 */
function StanceBadge({ stance, size = "sm" }: { stance: Stance; size?: "sm" | "corner" }) {
  const { bg, Icon } = STANCE_BADGE[stance];
  const dims = size === "sm" ? "w-4 h-4" : "w-3.5 h-3.5 border-2 border-slate-900";
  const iconSize = size === "sm" ? 11 : 9;
  return (
    <span className={`${dims} rounded-full flex items-center justify-center flex-shrink-0 ${bg}`}>
      <Icon size={iconSize} strokeWidth={3} className="text-white" />
    </span>
  );
}

/**
 * Compact "Consensus Meter" for the Multi-Persona Debate feature: one row
 * per contested claim (from backend services/consensus_meter.py), showing
 * every persona's real stance. Each persona avatar keeps its own identity
 * color; the stance (agree/disagree/mixed) is shown as a separate small
 * icon badge overlaid at the avatar's corner, so persona identity and
 * stance never compete for the same pixels.
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
              <StanceBadge stance={stance} size="sm" />
              {LABEL[stance]}
            </span>
          ))}
        </div>

        {/* Claims */}
        <div className="space-y-3">
          {claims.map((claim, i) => (
            <div key={i} className="flex items-center justify-between gap-4 flex-wrap">
              <p className="text-slate-300 text-sm flex-1 min-w-[200px]">{claim.claim}</p>
              <div className="flex items-center gap-2.5 flex-wrap">
                {Object.entries(claim.stances).map(([personaKey, stance]) => {
                  const meta = personaMap[personaKey];
                  return (
                    <span
                      key={personaKey}
                      title={`${meta?.name ?? personaKey}: ${LABEL[stance] ?? stance}`}
                      className="relative inline-flex flex-shrink-0"
                    >
                      <span
                        className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-white ${meta?.color ?? "bg-slate-700"}`}
                      >
                        {meta?.initials ?? "??"}
                      </span>
                      <span className="absolute -bottom-1 -right-1">
                        <StanceBadge stance={stance} size="corner" />
                      </span>
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
