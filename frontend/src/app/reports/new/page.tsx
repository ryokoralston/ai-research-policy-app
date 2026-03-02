"use client";

import { useState, useRef, useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronRight, Search, Users, FileQuestion, CheckCircle } from "lucide-react";
import { api, postStream } from "@/lib/api";
import { countWords, parseWordRange, wordCountColor } from "@/lib/wordCount";
import StreamingText from "@/components/ui/StreamingText";
import LoadingSpinner from "@/components/ui/LoadingSpinner";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const AUDIENCE_OPTIONS = [
  "Congressional staff and members",
  "Executive Branch / NSC Staff",
  "Senior Leadership / C-Suite",
  "Internal Use",
  "Academic / Research Community",
  "General Public",
  "Allied Government Partners",
  "Media / Press",
];

const REPORT_TYPES = [
  {
    id: "congressional_brief",
    label: "Congressional Brief",
    desc: "Multi-section briefing for Congress with background, findings, legislative options, and recommendations. ~2000 words.",
  },
  {
    id: "policy_memo",
    label: "Policy Memo",
    desc: "Direct, action-oriented memo for executive officials. Bottom line up front, options with tradeoffs. ~1000 words.",
  },
  {
    id: "risk_assessment",
    label: "Risk Assessment",
    desc: "Structured AI risk analysis with 6 scored dimensions, scenarios, and mitigation options. ~1500 words.",
  },
];

type SourceType = "none" | "research" | "debate";

interface DebateOption {
  id: string;
  topic: string;
  status: string;
  created_at: string;
}

interface ResearchSession {
  id: string;
  query: string;
  status: string;
  created_at: string;
}

const SOURCE_OPTIONS: { type: SourceType; icon: React.ElementType; label: string; desc: string }[] = [
  { type: "none",     icon: FileQuestion, label: "Write Report",     desc: "Write your report from scratch with template." },
  { type: "research", icon: Search,       label: "Research Session", desc: "Use a completed web research session." },
  { type: "debate",   icon: Users,        label: "Policy Debate",    desc: "Use a completed multi-persona debate." },
];

function NewReportForm() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Detect source type from URL params
  const initialDebateId  = searchParams.get("debate_id")  || "";
  const initialSessionId = searchParams.get("session_id") || "";
  const initialSourceType: SourceType =
    initialDebateId  ? "debate"   :
    initialSessionId ? "research" : "none";

  const [step, setStep]           = useState<1 | 2 | 3>(1);
  const [sourceType, setSourceType] = useState<SourceType>(initialSourceType);
  const [sessionId, setSessionId] = useState(initialSessionId);
  const [debateId, setDebateId]   = useState(initialDebateId);
  const [debates, setDebates]     = useState<DebateOption[]>([]);
  const [debatesLoading, setDebatesLoading] = useState(false);
  const [sessions, setSessions]   = useState<ResearchSession[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);

  const [reportType, setReportType]               = useState("congressional_brief");
  const [title, setTitle]                         = useState("");
  const [audience, setAudience]                   = useState("Congressional staff and members");
  const [customInstructions, setCustomInstructions] = useState("");

  const [generating, setGenerating]     = useState(false);
  const [currentSection, setCurrentSection] = useState("");
  const [outputText, setOutputText]     = useState("");
  const [reportId, setReportId]         = useState<string | null>(null);
  const [error, setError]               = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Template editor mode (No Source)
  const [templateMode, setTemplateMode] = useState(false);
  const [templateSections, setTemplateSections] = useState<
    { key: string; title: string; instructions: string }[]
  >([]);
  const [sectionContents, setSectionContents] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // Load completed debates when "debate" source type is selected
  useEffect(() => {
    if (sourceType !== "debate") return;
    setDebatesLoading(true);
    fetch(`${BASE_URL}/api/debate/`)
      .then((r) => r.json())
      .then((data: DebateOption[]) => {
        setDebates(data.filter((d) => d.status === "complete"));
        setDebatesLoading(false);
      })
      .catch(() => setDebatesLoading(false));
  }, [sourceType]);

  // Load completed research sessions when "research" source type is selected
  useEffect(() => {
    if (sourceType !== "research") return;
    setSessionsLoading(true);
    fetch(`${BASE_URL}/api/research/`)
      .then((r) => r.json())
      .then((data: ResearchSession[]) => {
        setSessions(data.filter((s) => s.status === "complete"));
        setSessionsLoading(false);
      })
      .catch(() => setSessionsLoading(false));
  }, [sourceType]);

  const handleOpenTemplate = async () => {
    setStep(3);
    setGenerating(true);
    setTemplateMode(true);
    try {
      const [templateData, draft] = await Promise.all([
        api.reports.template(reportType),
        api.reports.createDraft({ title, report_type: reportType }),
      ]);
      setTemplateSections(templateData.sections);
      setSectionContents(Object.fromEntries(templateData.sections.map((s) => [s.key, ""])));
      setReportId(draft.report_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load template");
      setTemplateMode(false);
    } finally {
      setGenerating(false);
    }
  };

  const handleSaveTemplate = async () => {
    if (!reportId) return;
    setSaving(true);
    setSaved(false);
    const sections = templateSections.map(
      (s) => `## ${s.title}\n\n${sectionContents[s.key] || "_[Not filled in]_"}`
    );
    const fullContent = `# ${title}\n\n` + sections.join("\n\n---\n\n");
    try {
      await api.reports.update(reportId, { content: fullContent, title });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save report");
    } finally {
      setSaving(false);
    }
  };

  const handleGenerate = async () => {
    if (!title.trim()) return;

    if (sourceType === "none") {
      await handleOpenTemplate();
      return;
    }

    setStep(3);
    setGenerating(true);
    setOutputText("");
    setError(null);
    abortRef.current = new AbortController();

    const payload: Record<string, unknown> = {
      report_type: reportType,
      title,
      audience,
      custom_instructions: customInstructions || undefined,
    };
    if (sourceType === "research" && sessionId) payload.session_id = sessionId;
    if (sourceType === "debate"   && debateId)  payload.debate_id  = debateId;

    try {
      await postStream(
        `${BASE_URL}/api/reports/generate`,
        payload,
        (event, data) => {
          const d = data as Record<string, unknown>;
          if (event === "section_start") {
            setCurrentSection(d.title as string);
          } else if (event === "token") {
            setOutputText((prev) => prev + (d.text as string));
          } else if (event === "complete") {
            setReportId(d.report_id as string);
            setGenerating(false);
            setCurrentSection("");
          } else if (event === "error") {
            setError(d.message as string);
            setGenerating(false);
          }
        },
        abortRef.current.signal
      );
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        setError(err.message);
        setGenerating(false);
      }
    }
  };

  const selectedDebate = debates.find((d) => d.id === debateId);

  return (
    <div className="p-8 max-w-4xl mx-auto">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-slate-100 mb-1">Generate Report</h1>
        {/* Step indicator */}
        <div className="flex items-center gap-2 mt-4 text-sm">
          {["Source", "Configure", "Generate"].map((label, i) => (
            <div key={label} className="flex items-center gap-2">
              <div
                className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${
                  step > i + 1 || step === i + 1
                    ? "bg-blue-600 text-white"
                    : "bg-slate-800 text-slate-500"
                }`}
              >
                {i + 1}
              </div>
              <span className={step === i + 1 ? "text-slate-100" : "text-slate-500"}>{label}</span>
              {i < 2 && <ChevronRight size={14} className="text-slate-600" />}
            </div>
          ))}
        </div>
      </div>

      {/* ── Step 1: Source ── */}
      {step === 1 && (
        <div className="space-y-6">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 space-y-4">
            <h2 className="text-slate-100 font-semibold">Source Material</h2>

            {/* Source type picker */}
            <div className="grid grid-cols-3 gap-3">
              {SOURCE_OPTIONS.map(({ type, icon: Icon, label, desc }) => (
                <button
                  key={type}
                  type="button"
                  onClick={() => { setSourceType(type); setSessionId(""); setDebateId(""); }}
                  className={`flex flex-col gap-2 p-4 rounded-lg border text-left transition-colors ${
                    sourceType === type
                      ? "border-blue-500 bg-blue-900/20"
                      : "border-slate-700 hover:border-slate-600"
                  }`}
                >
                  <Icon size={18} className={sourceType === type ? "text-blue-400" : "text-slate-500"} />
                  <p className="text-slate-100 text-sm font-medium">{label}</p>
                  <p className="text-slate-400 text-xs leading-snug">{desc}</p>
                </button>
              ))}
            </div>

            {/* Research session picker */}
            {sourceType === "research" && (
              <div className="pt-2">
                <label className="block text-sm text-slate-400 mb-2">Select a Research Session</label>
                {sessionsLoading ? (
                  <div className="flex items-center gap-2 text-slate-500 text-sm py-2">
                    <LoadingSpinner size="sm" /> Loading sessions...
                  </div>
                ) : sessions.length === 0 ? (
                  <p className="text-slate-500 text-sm py-2">
                    No completed research sessions found.{" "}
                    <a href="/research" className="text-blue-400 hover:underline">Go to Research Agent →</a>
                  </p>
                ) : (
                  <div className="space-y-2 max-h-80 overflow-y-scroll pr-2 scrollbar-thin scrollbar-thumb-slate-600 scrollbar-track-slate-800 rounded">
                    {sessions.map((s) => (
                      <button
                        key={s.id}
                        type="button"
                        onClick={() => setSessionId(s.id)}
                        className={`w-full text-left px-4 py-3 rounded-lg border transition-colors ${
                          sessionId === s.id
                            ? "border-blue-500 bg-blue-900/20"
                            : "border-slate-700 hover:border-slate-600"
                        }`}
                      >
                        <p className="text-slate-100 text-sm font-medium line-clamp-1">{s.query}</p>
                        <p className="text-slate-500 text-xs mt-0.5">
                          {new Date(s.created_at).toLocaleDateString("ja-JP")}
                        </p>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Debate picker */}
            {sourceType === "debate" && (
              <div className="pt-2">
                <label className="block text-sm text-slate-400 mb-2">Select a Debate</label>
                {debatesLoading ? (
                  <div className="flex items-center gap-2 text-slate-500 text-sm py-2">
                    <LoadingSpinner size="sm" /> Loading debates...
                  </div>
                ) : debates.length === 0 ? (
                  <p className="text-slate-500 text-sm py-2">
                    No completed debates found.{" "}
                    <a href="/debate" className="text-blue-400 hover:underline">Start a debate →</a>
                  </p>
                ) : (
                  <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
                    {debates.map((d) => (
                      <button
                        key={d.id}
                        type="button"
                        onClick={() => setDebateId(d.id)}
                        className={`w-full text-left px-4 py-3 rounded-lg border transition-colors ${
                          debateId === d.id
                            ? "border-blue-500 bg-blue-900/20"
                            : "border-slate-700 hover:border-slate-600"
                        }`}
                      >
                        <p className="text-slate-100 text-sm font-medium line-clamp-1">{d.topic}</p>
                        <p className="text-slate-500 text-xs mt-0.5">
                          {new Date(d.created_at).toLocaleDateString()}
                        </p>
                      </button>
                    ))}
                  </div>
                )}

                {/* Show selected debate topic as title suggestion */}
                {selectedDebate && !title && (
                  <p className="text-xs text-slate-500 mt-2">
                    Tip: You can use{" "}
                    <button
                      type="button"
                      className="text-blue-400 hover:underline"
                      onClick={() => { setStep(2); setTitle(selectedDebate.topic); }}
                    >
                      &ldquo;{selectedDebate.topic.slice(0, 40)}{selectedDebate.topic.length > 40 ? "…" : ""}&rdquo;
                    </button>
                    {" "}as the report title in the next step.
                  </p>
                )}
              </div>
            )}
          </div>

          <div className="flex justify-end">
            <button
              onClick={() => setStep(2)}
              disabled={
                (sourceType === "research" && !sessionId.trim()) ||
                (sourceType === "debate"   && !debateId)
              }
              className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-6 py-2.5 rounded-lg text-sm font-medium transition-colors"
            >
              Next →
            </button>
          </div>
        </div>
      )}

      {/* ── Step 2: Configure ── */}
      {step === 2 && (
        <div className="space-y-6">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
            <h2 className="text-slate-100 font-semibold mb-4">Report Type</h2>
            <div className="space-y-3">
              {REPORT_TYPES.map((t) => (
                <label
                  key={t.id}
                  className={`flex items-start gap-4 p-4 rounded-lg border cursor-pointer transition-colors ${
                    reportType === t.id
                      ? "border-blue-500 bg-blue-900/20"
                      : "border-slate-700 hover:border-slate-600"
                  }`}
                >
                  <input
                    type="radio"
                    value={t.id}
                    checked={reportType === t.id}
                    onChange={() => setReportType(t.id)}
                    className="mt-0.5"
                  />
                  <div>
                    <p className="text-slate-100 font-medium text-sm">{t.label}</p>
                    <p className="text-slate-400 text-xs mt-1">{t.desc}</p>
                  </div>
                </label>
              ))}
            </div>
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 space-y-4">
            <h2 className="text-slate-100 font-semibold">Report Details</h2>
            <div>
              <label className="block text-sm text-slate-400 mb-2">Title *</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. AI Governance Risks in Autonomous Weapons Systems"
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-2">Audience</label>
              <select
                value={AUDIENCE_OPTIONS.includes(audience) ? audience : "custom"}
                onChange={(e) => {
                  if (e.target.value !== "custom") setAudience(e.target.value);
                }}
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
              >
                {AUDIENCE_OPTIONS.map((opt) => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
                <option value="custom">Other (custom)...</option>
              </select>
              {!AUDIENCE_OPTIONS.includes(audience) && (
                <input
                  type="text"
                  value={audience}
                  onChange={(e) => setAudience(e.target.value)}
                  placeholder="Enter custom audience..."
                  className="mt-2 w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500"
                />
              )}
            </div>
            <div>
              <label className="block text-sm text-slate-400 mb-2">Additional Instructions (optional)</label>
              <textarea
                value={customInstructions}
                onChange={(e) => setCustomInstructions(e.target.value)}
                placeholder="Any specific focus areas, tone, or constraints..."
                rows={3}
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
              />
            </div>
          </div>

          <div className="flex justify-between">
            <button
              onClick={() => setStep(1)}
              className="text-slate-400 hover:text-white px-4 py-2.5 text-sm transition-colors"
            >
              ← Back
            </button>
            <button
              onClick={handleGenerate}
              disabled={!title.trim()}
              className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-6 py-2.5 rounded-lg text-sm font-medium transition-colors"
            >
              {sourceType === "none" ? "Open Template →" : "Generate Report →"}
            </button>
          </div>
        </div>
      )}

      {/* ── Step 3: Generation or Template Editor ── */}
      {step === 3 && (
        <div className="space-y-4">
          {error && (
            <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300 text-sm">
              {error}
            </div>
          )}

          {/* ── Template Editor Mode ── */}
          {templateMode && (
            <>
              {generating ? (
                <div className="flex items-center gap-3 text-sm text-slate-400 py-8 justify-center">
                  <LoadingSpinner size="sm" />
                  <span>Loading template...</span>
                </div>
              ) : (
                <>
                  <div className="mb-2">
                    <p className="text-slate-400 text-sm">
                      Fill in each section below. The guide text in each card explains what to write.
                    </p>
                  </div>

                  <div className="space-y-4">
                    {templateSections.map((section, i) => {
                      const wc = countWords(sectionContents[section.key] || "");
                      const range = parseWordRange(section.instructions);
                      const colorClass = wordCountColor(wc, range);
                      return (
                        <div
                          key={section.key}
                          className="bg-slate-900 border border-slate-800 rounded-xl p-5"
                        >
                          <div className="flex items-center gap-2 mb-2">
                            <span className="w-5 h-5 rounded-full bg-slate-700 text-slate-400 text-xs flex items-center justify-center flex-shrink-0">
                              {i + 1}
                            </span>
                            <h3 className="text-slate-100 font-semibold text-sm">{section.title}</h3>
                          </div>
                          <p className="text-slate-500 text-xs mb-3 leading-relaxed italic">
                            {section.instructions}
                          </p>
                          <textarea
                            value={sectionContents[section.key] || ""}
                            onChange={(e) =>
                              setSectionContents((prev) => ({ ...prev, [section.key]: e.target.value }))
                            }
                            rows={5}
                            placeholder={`Write the ${section.title} section here...`}
                            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2.5 text-sm
                                       text-slate-100 placeholder-slate-600 focus:outline-none focus:border-blue-500
                                       resize-y transition-colors"
                          />
                          <div className="flex justify-end mt-1.5">
                            <span className={`text-xs ${colorClass}`}>
                              {wc}{range ? ` / ${range.min}–${range.max} words` : " words"}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <div className="flex items-center gap-3 pt-2">
                    <button
                      onClick={handleSaveTemplate}
                      disabled={saving || saved}
                      className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50
                                 disabled:cursor-not-allowed text-white px-5 py-2.5 rounded-lg text-sm font-medium
                                 transition-colors"
                    >
                      {saving ? (
                        <><LoadingSpinner size="sm" /> Saving...</>
                      ) : saved ? (
                        <><CheckCircle size={14} /> Saved</>
                      ) : (
                        "Save Report"
                      )}
                    </button>

                    {reportId && (
                      <>
                        <button
                          onClick={() => router.push(`/reports/${reportId}`)}
                          className="bg-slate-800 hover:bg-slate-700 text-slate-100 px-5 py-2.5 rounded-lg text-sm font-medium transition-colors"
                        >
                          View Report
                        </button>
                        <button
                          onClick={() => router.push("/reports")}
                          className="text-slate-400 hover:text-white text-sm transition-colors"
                        >
                          All reports
                        </button>
                      </>
                    )}
                  </div>
                </>
              )}
            </>
          )}

          {/* ── AI Generation Mode ── */}
          {!templateMode && (
            <>
              {generating && (
                <div className="flex items-center gap-3 text-sm text-slate-400">
                  <LoadingSpinner size="sm" />
                  <span>
                    {currentSection ? `Writing: ${currentSection}` : "Starting generation..."}
                  </span>
                </div>
              )}

              <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 min-h-96">
                {outputText ? (
                  <StreamingText text={outputText} />
                ) : (
                  <div className="flex items-center justify-center h-48 text-slate-600 text-sm">
                    <LoadingSpinner />
                  </div>
                )}
              </div>

              {!generating && reportId && (
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => router.push(`/reports/${reportId}`)}
                    className="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2.5 rounded-lg text-sm font-medium transition-colors"
                  >
                    View Report
                  </button>
                  <a
                    href={`${BASE_URL}/api/reports/${reportId}/export`}
                    className="bg-slate-800 hover:bg-slate-700 text-slate-100 px-5 py-2.5 rounded-lg text-sm font-medium transition-colors"
                  >
                    Export Markdown
                  </a>
                  <button
                    onClick={() => router.push("/reports")}
                    className="text-slate-400 hover:text-white text-sm transition-colors"
                  >
                    View all reports
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function NewReportPage() {
  return (
    <Suspense fallback={<div className="p-8"><LoadingSpinner /></div>}>
      <NewReportForm />
    </Suspense>
  );
}
