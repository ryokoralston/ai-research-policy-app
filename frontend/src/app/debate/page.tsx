"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Users, Download, ChevronDown, ChevronRight, Loader2, FileText } from "lucide-react";
import { authFetch } from "@/lib/api";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Persona metadata ────────────────────────────────────────────────────────

interface PersonaMeta {
  key: string;
  name: string;
  title: string;
  initials: string;
  color: string;    // Tailwind bg class
  textColor: string; // Tailwind text class
}

const PERSONA_LIST: PersonaMeta[] = [
  { key: "safety_researcher", name: "Dr. Sarah Chen",        title: "AI Safety Researcher",       initials: "SC", color: "bg-violet-600",  textColor: "text-violet-100" },
  { key: "tech_ceo",          name: "Marcus Webb",            title: "Tech Industry CEO",           initials: "MW", color: "bg-blue-600",    textColor: "text-blue-100"   },
  { key: "military",          name: "Lt. Gen. Patricia Morrison", title: "National Security Strategist", initials: "PM", color: "bg-slate-600",  textColor: "text-slate-100" },
  { key: "civil_rights",      name: "Aisha Okonkwo",          title: "Digital Rights Advocate",    initials: "AO", color: "bg-rose-600",    textColor: "text-rose-100"   },
  { key: "intl_relations",    name: "Prof. Hiroshi Tanaka",   title: "Int'l Relations Scholar",    initials: "HT", color: "bg-teal-600",    textColor: "text-teal-100"   },
  { key: "economist",         name: "Dr. Elena Vasquez",      title: "Labor Economist",            initials: "EV", color: "bg-amber-600",   textColor: "text-amber-100"  },
  { key: "ethicist",          name: "Rev. James Callahan",    title: "Ethicist & Philosopher",     initials: "JC", color: "bg-emerald-600", textColor: "text-emerald-100"},
  { key: "regulator",         name: "Commissioner Robert Kim","title": "Government Regulator",      initials: "RK", color: "bg-orange-600",  textColor: "text-orange-100" },
  { key: "global_south",      name: "Dr. Priya Patel",        title: "Developing World Advocate",  initials: "PP", color: "bg-cyan-600",    textColor: "text-cyan-100"   },
  { key: "accelerationist",   name: "Dr. Alex Summers",       title: "AI Accelerationist",         initials: "AS", color: "bg-red-600",     textColor: "text-red-100"    },
];

const PERSONA_MAP = Object.fromEntries(PERSONA_LIST.map((p) => [p.key, p]));

const ROUNDS = [
  { num: 1, name: "Opening Positions" },
  { num: 2, name: "Key Concerns" },
  { num: 3, name: "Cross-Response" },
  { num: 4, name: "Policy Recommendations" },
];

// ── Types ───────────────────────────────────────────────────────────────────

interface Argument {
  personaKey: string;
  personaName: string;
  roundNumber: number;
  roundName: string;
  content: string;
  streaming: boolean;
}

interface DebateState {
  debateId: string | null;
  status: "idle" | "running" | "complete" | "error";
  currentRound: number;
  currentPersona: string | null;
  arguments: Argument[];
  synthesis: string;
  error: string | null;
}

// ── Past debates type ───────────────────────────────────────────────────────

interface PastDebate {
  id: string;
  topic: string;
  status: string;
  created_at: string;
}

// ── Utility: SSE stream from GET ────────────────────────────────────────────

async function consumeGetSSE(
  url: string,
  onEvent: (event: string, data: unknown) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await authFetch(url, { signal });
  if (!res.ok || !res.body) throw new Error(`Stream failed: ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    let currentEvent = "message";
    let currentData = "";

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith("data: ")) {
        currentData = line.slice(6).trim();
      } else if (line === "") {
        if (currentData) {
          try { onEvent(currentEvent, JSON.parse(currentData)); }
          catch { onEvent(currentEvent, currentData); }
          currentEvent = "message";
          currentData = "";
        }
      }
    }
  }
}

// ── Export helpers ───────────────────────────────────────────────────────────

function buildMarkdown(topic: string, args: Argument[], synthesis: string): string {
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

function buildPlainText(topic: string, args: Argument[], synthesis: string): string {
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
    lines.push(`${arg.personaName} (${PERSONA_MAP[arg.personaKey]?.title ?? ""})`);
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

function downloadBlob(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function exportAsPdf(topic: string, args: Argument[], synthesis: string) {
  const sep = '<hr style="border:none;border-top:1px solid #ccc;margin:1.5em 0">';
  const ROUND_COLORS: Record<number, string> = { 1: "#4f46e5", 2: "#0891b2", 3: "#b45309", 4: "#15803d" };

  let bodyHtml = `<h1 style="font-size:1.4em;color:#1e293b;margin-bottom:0.2em">AI Policy Debate</h1>
<p style="color:#64748b;font-size:0.95em;margin-top:0">${topic}</p>${sep}`;

  let lastRound = 0;
  for (const arg of args) {
    const meta = PERSONA_MAP[arg.personaKey];
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


// ── Main Component ──────────────────────────────────────────────────────────

export default function DebatePage() {
  const [topic, setTopic] = useState("");
  const [selectedPersonas, setSelectedPersonas] = useState<Set<string>>(
    new Set(PERSONA_LIST.map((p) => p.key))
  );
  const [showPersonaSelector, setShowPersonaSelector] = useState(false);
  const [debate, setDebate] = useState<DebateState>({
    debateId: null,
    status: "idle",
    currentRound: 0,
    currentPersona: null,
    arguments: [],
    synthesis: "",
    error: null,
  });
  const [pastDebates, setPastDebates] = useState<PastDebate[]>([]);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const router = useRouter();
  const bottomRef = useRef<HTMLDivElement>(null);

  // Load past debates on mount
  useEffect(() => {
    authFetch(`${BASE_URL}/api/debate/`)
      .then((r) => r.json())
      .then(setPastDebates)
      .catch(() => {});
  }, []);

  // Auto-scroll as content streams
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [debate.arguments, debate.synthesis]);

  // Close export menu on outside click
  useEffect(() => {
    if (!showExportMenu) return;
    const handler = () => setShowExportMenu(false);
    document.addEventListener("click", handler);
    return () => document.removeEventListener("click", handler);
  }, [showExportMenu]);

  const handleStartDebate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!topic.trim() || debate.status === "running") return;

    setDebate({
      debateId: null,
      status: "running",
      currentRound: 0,
      currentPersona: null,
      arguments: [],
      synthesis: "",
      error: null,
    });

    abortRef.current = new AbortController();

    try {
      // 1. Create debate
      const startRes = await authFetch(`${BASE_URL}/api/debate/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic: topic.trim(),
          persona_keys: Array.from(selectedPersonas),
        }),
        signal: abortRef.current.signal,
      });
      const { debate_id } = await startRes.json();
      setDebate((prev) => ({ ...prev, debateId: debate_id }));

      // 2. Stream SSE events
      await consumeGetSSE(
        `${BASE_URL}/api/debate/${debate_id}/stream`,
        (event, data) => {
          const d = data as Record<string, unknown>;

          if (event === "round_start") {
            setDebate((prev) => ({ ...prev, currentRound: d.round as number }));
          } else if (event === "persona_start") {
            const pKey = d.persona_key as string;
            const pName = d.persona_name as string;
            const roundNum = d.round as number;
            const roundName = ROUNDS.find((r) => r.num === roundNum)?.name ?? "";
            setDebate((prev) => ({
              ...prev,
              currentPersona: pKey,
              arguments: [
                ...prev.arguments,
                {
                  personaKey: pKey,
                  personaName: pName,
                  roundNumber: roundNum,
                  roundName,
                  content: "",
                  streaming: true,
                },
              ],
            }));
          } else if (event === "token") {
            const pKey = d.persona_key as string;
            const text = d.text as string;
            const round = d.round as number;
            if (round === 0) {
              // Synthesis token
              setDebate((prev) => ({ ...prev, synthesis: prev.synthesis + text }));
            } else {
              setDebate((prev) => {
                const args = [...prev.arguments];
                // Update last arg for this persona
                for (let i = args.length - 1; i >= 0; i--) {
                  if (args[i].personaKey === pKey && args[i].streaming) {
                    args[i] = { ...args[i], content: args[i].content + text };
                    break;
                  }
                }
                return { ...prev, arguments: args };
              });
            }
          } else if (event === "persona_end") {
            const pKey = d.persona_key as string;
            setDebate((prev) => {
              const args = [...prev.arguments];
              for (let i = args.length - 1; i >= 0; i--) {
                if (args[i].personaKey === pKey && args[i].streaming) {
                  args[i] = { ...args[i], streaming: false };
                  break;
                }
              }
              return { ...prev, currentPersona: null, arguments: args };
            });
          } else if (event === "synthesis_start") {
            setDebate((prev) => ({ ...prev, currentPersona: "moderator" }));
          } else if (event === "complete") {
            setDebate((prev) => ({ ...prev, status: "complete", currentPersona: null }));
            // Refresh past debates list
            authFetch(`${BASE_URL}/api/debate/`)
              .then((r) => r.json())
              .then(setPastDebates)
              .catch(() => {});
          } else if (event === "error") {
            setDebate((prev) => ({
              ...prev,
              status: "error",
              error: (d.message as string) || "Unknown error",
            }));
          }
        },
        abortRef.current.signal
      );
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        setDebate((prev) => ({ ...prev, status: "error", error: err.message }));
      }
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
    setDebate((prev) => ({ ...prev, status: "idle" }));
  };

  const handleLoadPast = async (id: string) => {
    try {
      const res = await authFetch(`${BASE_URL}/api/debate/${id}`);
      const data = await res.json();
      const args: Argument[] = (data.arguments ?? []).map((a: Record<string, unknown>) => ({
        personaKey: a.persona_key as string,
        personaName: a.persona_name as string,
        roundNumber: a.round_number as number,
        roundName: a.round_name as string,
        content: a.content as string,
        streaming: false,
      }));
      setTopic(data.topic as string);
      setDebate({
        debateId: id,
        status: "complete",
        currentRound: 4,
        currentPersona: null,
        arguments: args,
        synthesis: (data.synthesis as string) ?? "",
        error: null,
      });
    } catch {
      // ignore
    }
  };

  const handleDeletePast = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    await authFetch(`${BASE_URL}/api/debate/${id}`, { method: "DELETE" });
    setPastDebates((prev) => prev.filter((d) => d.id !== id));
    if (debate.debateId === id) {
      setDebate({ debateId: null, status: "idle", currentRound: 0, currentPersona: null, arguments: [], synthesis: "", error: null });
    }
  };

  const togglePersona = (key: string) => {
    setSelectedPersonas((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        if (next.size > 2) next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const isRunning = debate.status === "running";

  // Group arguments by round
  const byRound = debate.arguments.reduce<Record<number, Argument[]>>((acc, arg) => {
    if (!acc[arg.roundNumber]) acc[arg.roundNumber] = [];
    acc[arg.roundNumber].push(arg);
    return acc;
  }, {});

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── Past debates sidebar ── */}
      <aside className="w-56 flex-shrink-0 border-r border-slate-800 flex flex-col overflow-hidden">
        <div className="p-3 border-b border-slate-800">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Past Debates</p>
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {pastDebates.length === 0 && (
            <p className="text-xs text-slate-600 p-2">No debates yet.</p>
          )}
          {pastDebates.map((d) => (
            <button
              key={d.id}
              onClick={() => handleLoadPast(d.id)}
              className="w-full text-left px-2 py-2 rounded-md hover:bg-slate-800 transition-colors group"
            >
              <p className="text-xs text-slate-300 line-clamp-2 leading-snug">{d.topic}</p>
              <div className="flex items-center justify-between mt-1">
                <span className={`text-xs ${d.status === "complete" ? "text-emerald-500" : "text-slate-500"}`}>
                  {d.status}
                </span>
                <button
                  onClick={(e) => handleDeletePast(d.id, e)}
                  className="text-slate-600 hover:text-red-400 text-xs opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  ×
                </button>
              </div>
            </button>
          ))}
        </div>
      </aside>

      {/* ── Main content ── */}
      <div className="flex-1 overflow-y-auto">
        <div className="p-8 max-w-4xl mx-auto">
          <div className="mb-6">
            <h1 className="text-2xl font-bold text-slate-100 mb-1 flex items-center gap-2">
              <Users size={22} className="text-blue-400" />
              Multi-Persona Policy Debate
            </h1>
            <p className="text-slate-400 text-sm">
              10 AI policy experts debate your topic across 4 structured rounds.
            </p>
            <p className="text-slate-500 text-xs mt-1">
              ※ All personas are entirely fictional characters created for debate simulation purposes. Any resemblance to real individuals is coincidental.
            </p>
          </div>

          {/* ── Form ── */}
          <form onSubmit={handleStartDebate} className="mb-8 space-y-3">
            <div className="flex gap-3">
              <input
                type="text"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="e.g. Should the U.S. implement a federal AI licensing regime?"
                className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500"
                disabled={isRunning}
              />
              {isRunning ? (
                <button
                  type="button"
                  onClick={handleStop}
                  className="bg-red-600 hover:bg-red-700 text-white px-5 py-3 rounded-lg text-sm font-medium transition-colors"
                >
                  Stop
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!topic.trim() || selectedPersonas.size < 2}
                  className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-5 py-3 rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
                >
                  Start Debate
                </button>
              )}
            </div>

            {/* Persona selector */}
            <div>
              <button
                type="button"
                onClick={() => setShowPersonaSelector((v) => !v)}
                className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors"
              >
                {showPersonaSelector ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                Personas ({selectedPersonas.size}/{PERSONA_LIST.length} selected)
              </button>

              {showPersonaSelector && (
                <div className="mt-2">
                  <p className="text-xs text-slate-600 mb-2 italic">※ All personas below are fictional and do not represent any real individuals or organizations.</p>
                  <div className="flex flex-wrap gap-2">
                  {PERSONA_LIST.map((p) => {
                    const selected = selectedPersonas.has(p.key);
                    return (
                      <button
                        key={p.key}
                        type="button"
                        onClick={() => togglePersona(p.key)}
                        disabled={isRunning}
                        className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-all ${
                          selected
                            ? "border-blue-500 bg-blue-600/20 text-blue-300"
                            : "border-slate-700 bg-slate-900 text-slate-500 hover:text-slate-300"
                        }`}
                      >
                        <span className={`w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold ${p.color} ${p.textColor}`}>
                          {p.initials[0]}
                        </span>
                        {p.name}
                      </button>
                    );
                  })}
                  </div>
                </div>
              )}
            </div>
          </form>

          {/* ── Error ── */}
          {debate.error && (
            <div className="mb-6 bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300 text-sm">
              {debate.error}
            </div>
          )}

          {/* ── Debate content ── */}
          {debate.arguments.length > 0 && (
            <div className="space-y-8">
              {/* Rounds */}
              {ROUNDS.map((round) => {
                const roundArgs = byRound[round.num] ?? [];
                if (roundArgs.length === 0 && debate.currentRound < round.num) return null;
                const isCurrentRound = debate.currentRound === round.num;

                return (
                  <section key={round.num}>
                    <div className="flex items-center gap-3 mb-4">
                      <span className={`px-2.5 py-1 rounded-full text-xs font-semibold ${
                        isCurrentRound && isRunning
                          ? "bg-blue-600 text-white"
                          : "bg-slate-800 text-slate-400"
                      }`}>
                        Round {round.num}
                      </span>
                      <h2 className="text-sm font-semibold text-slate-300">{round.name}</h2>
                      {isCurrentRound && isRunning && (
                        <Loader2 size={14} className="text-blue-400 animate-spin" />
                      )}
                    </div>

                    <div className="space-y-3">
                      {roundArgs.map((arg, i) => {
                        const meta = PERSONA_MAP[arg.personaKey];
                        const isActive = arg.streaming && debate.currentPersona === arg.personaKey;
                        return (
                          <div
                            key={`${arg.personaKey}-${round.num}-${i}`}
                            className={`bg-slate-900 border rounded-lg p-4 transition-all ${
                              isActive
                                ? "border-blue-500/50 shadow-[0_0_12px_rgba(59,130,246,0.15)]"
                                : "border-slate-800"
                            }`}
                          >
                            <div className="flex items-center gap-2.5 mb-2">
                              <span className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 ${meta?.color ?? "bg-slate-700"} ${meta?.textColor ?? "text-slate-100"}`}>
                                {meta?.initials ?? "??"}
                              </span>
                              <div>
                                <p className="text-slate-100 text-sm font-medium leading-tight">{arg.personaName}</p>
                                <p className="text-slate-500 text-xs">{meta?.title ?? ""}</p>
                              </div>
                              {isActive && (
                                <span className="ml-auto text-xs text-blue-400 flex items-center gap-1">
                                  <Loader2 size={11} className="animate-spin" /> speaking
                                </span>
                              )}
                            </div>
                            <p className="text-slate-300 text-sm leading-relaxed whitespace-pre-wrap">
                              {arg.content}
                              {isActive && (
                                <span className="inline-block w-1.5 h-3.5 bg-blue-400 animate-pulse ml-0.5 align-text-bottom" />
                              )}
                            </p>
                          </div>
                        );
                      })}

                      {/* Placeholder for in-progress round with no args yet */}
                      {roundArgs.length === 0 && isCurrentRound && isRunning && (
                        <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 flex items-center gap-2 text-slate-500 text-sm">
                          <Loader2 size={14} className="animate-spin" />
                          Preparing round...
                        </div>
                      )}
                    </div>
                  </section>
                );
              })}

              {/* Synthesis */}
              {(debate.synthesis || (debate.currentPersona === "moderator" && isRunning)) && (
                <section>
                  <div className="flex items-center gap-3 mb-4">
                    <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-900/50 text-emerald-400">
                      Synthesis
                    </span>
                    <h2 className="text-sm font-semibold text-slate-300">Moderator Summary</h2>
                    {debate.currentPersona === "moderator" && isRunning && (
                      <Loader2 size={14} className="text-emerald-400 animate-spin" />
                    )}
                  </div>
                  <div className={`bg-slate-900 border rounded-lg p-5 transition-all ${
                    debate.currentPersona === "moderator" && isRunning
                      ? "border-emerald-500/50 shadow-[0_0_12px_rgba(52,211,153,0.1)]"
                      : "border-slate-800"
                  }`}>
                    <div className="flex items-center gap-2.5 mb-3">
                      <span className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold bg-emerald-700 text-emerald-100 flex-shrink-0">
                        M
                      </span>
                      <div>
                        <p className="text-slate-100 text-sm font-medium">Moderator</p>
                        <p className="text-slate-500 text-xs">Conference Synthesis</p>
                      </div>
                    </div>
                    <div className="prose prose-invert prose-sm max-w-none text-slate-300">
                      <p className="whitespace-pre-wrap">{debate.synthesis}
                        {debate.currentPersona === "moderator" && isRunning && (
                          <span className="inline-block w-1.5 h-3.5 bg-emerald-400 animate-pulse ml-0.5 align-text-bottom" />
                        )}
                      </p>
                    </div>
                  </div>
                </section>
              )}

              {/* Action buttons */}
              {debate.status === "complete" && (
                <div className="flex justify-end gap-2 pt-2">
                  {/* Generate Report */}
                  <button
                    onClick={() => router.push(`/reports/new?debate_id=${debate.debateId}`)}
                    className="flex items-center gap-2 text-sm bg-blue-600/20 hover:bg-blue-600/30 text-blue-400 px-4 py-2 rounded-lg transition-colors"
                  >
                    <FileText size={14} />
                    Generate Report
                  </button>

                  {/* Export dropdown */}
                  <div className="relative">
                    <button
                      onClick={(e) => { e.stopPropagation(); setShowExportMenu((v) => !v); }}
                      className="flex items-center gap-2 text-sm bg-slate-800 hover:bg-slate-700 text-slate-300 px-4 py-2 rounded-lg transition-colors"
                    >
                      <Download size={14} />
                      Export
                      <ChevronDown size={13} className={`transition-transform ${showExportMenu ? "rotate-180" : ""}`} />
                    </button>
                    {showExportMenu && (
                      <div className="absolute right-0 bottom-full mb-1 w-44 bg-slate-800 border border-slate-700 rounded-lg shadow-xl overflow-hidden z-10">
                        <button
                          onClick={() => { downloadBlob(buildMarkdown(topic, debate.arguments, debate.synthesis), `debate-${Date.now()}.md`, "text/markdown"); setShowExportMenu(false); }}
                          className="w-full text-left px-4 py-2.5 text-sm text-slate-300 hover:bg-slate-700 transition-colors"
                        >
                          Markdown (.md)
                        </button>
                        <button
                          onClick={() => { downloadBlob(buildPlainText(topic, debate.arguments, debate.synthesis), `debate-${Date.now()}.txt`, "text/plain"); setShowExportMenu(false); }}
                          className="w-full text-left px-4 py-2.5 text-sm text-slate-300 hover:bg-slate-700 transition-colors"
                        >
                          Plain Text (.txt)
                        </button>
                        <button
                          onClick={() => { exportAsPdf(topic, debate.arguments, debate.synthesis); setShowExportMenu(false); }}
                          className="w-full text-left px-4 py-2.5 text-sm text-slate-300 hover:bg-slate-700 transition-colors"
                        >
                          PDF (print dialog)
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              )}

              <div ref={bottomRef} />
            </div>
          )}

          {/* Empty state */}
          {debate.arguments.length === 0 && debate.status === "idle" && (
            <div className="text-center py-16 text-slate-600">
              <Users size={40} className="mx-auto mb-3 opacity-30" />
              <p className="text-sm">Enter a policy topic above to begin the debate.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
