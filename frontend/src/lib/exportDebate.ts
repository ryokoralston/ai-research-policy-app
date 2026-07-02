/**
 * Debate export helpers (G-3).
 *
 * Extracted from app/debate/page.tsx: buildMarkdown/buildPlainText/
 * downloadBlob/exportAsPdf are pure functions with no React/DOM state of
 * their own, so the page component can stay focused on rendering. The
 * `Argument` type moves here too (single source of truth for the shape these
 * functions consume) — the page imports it instead of redefining it.
 *
 * Deliberately NOT merged with components/ui/DownloadMenu.tsx: that's a
 * different export surface, and the verification cost of unifying the two
 * isn't justified for a relocate-only refactor.
 */

export interface Argument {
  personaKey: string;
  personaName: string;
  roundNumber: number;
  roundName: string;
  content: string;
  streaming: boolean;
}

/** Only the persona fields buildPlainText/exportAsPdf actually read. */
interface PersonaMetaLite {
  title?: string;
  initials?: string;
  color?: string;
}

export type PersonaMetaMap = Record<string, PersonaMetaLite>;

export function buildMarkdown(topic: string, args: Argument[], synthesis: string): string {
  const lines: string[] = [`# AI Policy Debate: ${topic}\n`];
  let lastRound = 0;
  for (const arg of args) {
    if (arg.roundNumber !== lastRound) {
      lines.push(`\n## Round ${arg.roundNumber}: ${arg.roundName}\n`);
      lastRound = arg.roundNumber;
    }
    lines.push(`### ${arg.personaName}\n\n${arg.content}\n`);
  }
  if (synthesis) {
    lines.push(`\n## Moderator Synthesis\n\n${synthesis}\n`);
  }
  return lines.join("\n");
}

export function buildPlainText(
  topic: string,
  args: Argument[],
  synthesis: string,
  personaMap: PersonaMetaMap
): string {
  const sep = "─".repeat(60);
  const lines: string[] = [`AI POLICY DEBATE`, `Topic: ${topic}`, sep, ""];
  let lastRound = 0;
  for (const arg of args) {
    if (arg.roundNumber !== lastRound) {
      lines.push(`ROUND ${arg.roundNumber}: ${arg.roundName.toUpperCase()}`);
      lines.push(sep);
      lines.push("");
      lastRound = arg.roundNumber;
    }
    lines.push(`${arg.personaName} (${personaMap[arg.personaKey]?.title ?? ""})`);
    lines.push(arg.content);
    lines.push("");
  }
  if (synthesis) {
    lines.push(sep);
    lines.push("MODERATOR SYNTHESIS");
    lines.push(sep);
    lines.push("");
    lines.push(synthesis);
    lines.push("");
  }
  return lines.join("\n");
}

export function downloadBlob(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function exportAsPdf(
  topic: string,
  args: Argument[],
  synthesis: string,
  personaMap: PersonaMetaMap
) {
  const sep = '<hr style="border:none;border-top:1px solid #ccc;margin:1.5em 0">';
  const ROUND_COLORS: Record<number, string> = { 1: "#4f46e5", 2: "#0891b2", 3: "#b45309", 4: "#15803d" };

  let bodyHtml = `<h1 style="font-size:1.4em;color:#1e293b;margin-bottom:0.2em">AI Policy Debate</h1>
<p style="color:#64748b;font-size:0.95em;margin-top:0">${topic}</p>${sep}`;

  let lastRound = 0;
  for (const arg of args) {
    const meta = personaMap[arg.personaKey];
    if (arg.roundNumber !== lastRound) {
      const color = ROUND_COLORS[arg.roundNumber] ?? "#374151";
      bodyHtml += `<h2 style="font-size:1em;color:${color};text-transform:uppercase;letter-spacing:0.05em;margin:1.5em 0 0.75em">Round ${arg.roundNumber}: ${arg.roundName}</h2>`;
      lastRound = arg.roundNumber;
    }
    const initials = meta?.initials ?? "??";
    const color = meta?.color?.replace("bg-", "") ?? "slate-600";
    const HEX: Record<string, string> = {
      "violet-600": "#7c3aed", "blue-600": "#2563eb", "slate-600": "#475569",
      "rose-600": "#e11d48", "teal-600": "#0d9488", "amber-600": "#d97706",
      "emerald-600": "#059669", "orange-600": "#ea580c", "cyan-600": "#0891b2",
      "red-600": "#dc2626",
    };
    const bgHex = HEX[color] ?? "#475569";
    bodyHtml += `
<div style="margin-bottom:1em;padding:0.9em 1em;border:1px solid #e2e8f0;border-radius:8px;page-break-inside:avoid">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:0.5em">
    <span style="display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;background:${bgHex};color:#fff;font-size:11px;font-weight:700;flex-shrink:0">${initials}</span>
    <div>
      <strong style="font-size:0.9em;color:#1e293b">${arg.personaName}</strong>
      <span style="color:#94a3b8;font-size:0.8em;margin-left:6px">${meta?.title ?? ""}</span>
    </div>
  </div>
  <p style="margin:0;font-size:0.88em;color:#334155;line-height:1.65;white-space:pre-wrap">${arg.content}</p>
</div>`;
  }

  if (synthesis) {
    bodyHtml += `${sep}
<h2 style="font-size:1em;color:#059669;text-transform:uppercase;letter-spacing:0.05em;margin:1.5em 0 0.75em">Moderator Synthesis</h2>
<div style="padding:0.9em 1em;border:1px solid #d1fae5;border-radius:8px;background:#f0fdf4">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:0.5em">
    <span style="display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;background:#059669;color:#fff;font-size:11px;font-weight:700">M</span>
    <strong style="font-size:0.9em;color:#1e293b">Moderator</strong>
  </div>
  <p style="margin:0;font-size:0.88em;color:#334155;line-height:1.65;white-space:pre-wrap">${synthesis}</p>
</div>`;
  }

  const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Debate: ${topic}</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:760px;margin:2em auto;padding:0 1.5em;color:#1e293b;font-size:14px}
  @media print{body{margin:0;padding:1cm}}
</style>
</head><body>${bodyHtml}</body></html>`;

  const win = window.open("", "_blank");
  if (!win) return;
  win.document.write(html);
  win.document.close();
  win.focus();
  setTimeout(() => { win.print(); }, 400);
}
