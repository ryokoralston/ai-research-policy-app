"use client";

import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { Shield, Plus } from "lucide-react";
import { api, postStream } from "@/lib/api";
import type { RiskAnalysis } from "@/lib/types";
import Badge from "@/components/ui/Badge";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import StreamingText from "@/components/ui/StreamingText";

const ANALYSIS_TYPES = [
  { id: "technology", label: "Technology" },
  { id: "policy", label: "Policy" },
  { id: "actor", label: "Actor/Organization" },
];

const SCORE_LABELS: Record<string, string> = {
  capability: "Capability",
  deployment: "Deployment Speed",
  governance: "Governance Gap",
  geopolitical: "Geopolitical Risk",
  misuse: "Misuse Potential",
  systemic: "Systemic Risk",
};

function ScoreBar({ label, score }: { label: string; score: number }) {
  const color = score >= 7 ? "bg-red-500" : score >= 5 ? "bg-amber-500" : "bg-green-500";
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-slate-400">{label}</span>
        <span className="text-slate-100 font-mono">{score}/10</span>
      </div>
      <div className="h-1.5 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${score * 10}%` }} />
      </div>
    </div>
  );
}

export default function AnalysisPage() {
  const router = useRouter();
  const [analyses, setAnalyses] = useState<RiskAnalysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [subject, setSubject] = useState("");
  const [analysisType, setAnalysisType] = useState("technology");
  const [context, setContext] = useState("");
  const [runWebResearch, setRunWebResearch] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [currentSection, setCurrentSection] = useState("");
  const [outputText, setOutputText] = useState("");
  const [scores, setScores] = useState<Record<string, number> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [completedAnalysisId, setCompletedAnalysisId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    api.analysis.list().then((data) => {
      setAnalyses(data as RiskAnalysis[]);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const handleGenerate = async () => {
    if (!subject.trim()) return;
    setGenerating(true);
    setOutputText("");
    setScores(null);
    setError(null);
    abortRef.current = new AbortController();

    try {
      await postStream(
        "http://localhost:8000/api/analysis/start",
        { subject, analysis_type: analysisType, context: context || undefined, run_web_research: runWebResearch },
        (event, data) => {
          const d = data as Record<string, unknown>;
          if (event === "section_start") {
            setCurrentSection(d.title as string);
          } else if (event === "token") {
            setOutputText((prev) => prev + (d.text as string));
          } else if (event === "scores") {
            setScores(d.scores as Record<string, number>);
          } else if (event === "complete") {
            setGenerating(false);
            setCurrentSection("");
            setCompletedAnalysisId(d.analysis_id as string);
            api.analysis.list().then((data) => setAnalyses(data as RiskAnalysis[]));
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

  const handleViewAnalysis = (id: string) => {
    router.push(`/analysis/${id}`);
  };

  return (
    <div className="p-8 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-slate-100 mb-1">Risk Analysis</h1>
          <p className="text-slate-400 text-sm">Structured AI risk assessment with scored dimensions</p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        >
          <Plus size={16} />
          New Analysis
        </button>
      </div>

      {/* New Analysis Form */}
      {showForm && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 mb-8 space-y-4">
          <h2 className="text-slate-100 font-semibold">Configure Analysis</h2>
          <div>
            <label className="block text-sm text-slate-400 mb-2">Subject *</label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="e.g. GPT-5 deployment, EU AI Act, OpenAI"
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500"
            />
          </div>
          <div className="flex gap-3">
            {ANALYSIS_TYPES.map((t) => (
              <label key={t.id} className={`flex items-center gap-2 px-4 py-2 rounded-lg border cursor-pointer transition-colors text-sm ${
                analysisType === t.id ? "border-blue-500 bg-blue-900/20 text-blue-300" : "border-slate-700 text-slate-400"
              }`}>
                <input type="radio" value={t.id} checked={analysisType === t.id} onChange={() => setAnalysisType(t.id)} className="hidden" />
                {t.label}
              </label>
            ))}
          </div>
          <div>
            <label className="block text-sm text-slate-400 mb-2">Additional Context (optional)</label>
            <textarea
              value={context}
              onChange={(e) => setContext(e.target.value)}
              placeholder="Any specific concerns or context to focus on..."
              rows={3}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
            />
          </div>
          <label className="flex items-center gap-3 cursor-pointer">
            <input type="checkbox" checked={runWebResearch} onChange={(e) => setRunWebResearch(e.target.checked)} />
            <span className="text-sm text-slate-300">Run web research first (recommended)</span>
          </label>
          <button
            onClick={handleGenerate}
            disabled={!subject.trim() || generating}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-6 py-2.5 rounded-lg text-sm font-medium transition-colors"
          >
            {generating ? "Analyzing..." : "Run Analysis"}
          </button>
        </div>
      )}

      {/* Live generation output */}
      {(generating || outputText) && showForm && (
        <div className="mb-8">
          {generating && (
            <div className="flex items-center gap-2 text-sm text-slate-400 mb-3">
              <LoadingSpinner size="sm" />
              <span>{currentSection ? `Writing: ${currentSection}` : "Starting..."}</span>
            </div>
          )}
          {error && (
            <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300 text-sm mb-4">{error}</div>
          )}
          <div className="grid grid-cols-3 gap-6">
            {scores && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
                <h3 className="text-slate-100 font-semibold text-sm mb-4">Risk Scores</h3>
                {Object.entries(scores).map(([key, val]) => (
                  <ScoreBar key={key} label={SCORE_LABELS[key] || key} score={val} />
                ))}
              </div>
            )}
            <div className={`bg-slate-900 border border-slate-800 rounded-xl p-5 ${scores ? "col-span-2" : "col-span-3"}`}>
              <StreamingText text={outputText} />
            </div>
          </div>
          {!generating && completedAnalysisId && (
            <div className="mt-4 flex items-center gap-3">
              <button
                onClick={() => router.push(`/analysis/${completedAnalysisId}`)}
                className="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2.5 rounded-lg text-sm font-medium transition-colors"
              >
                View Full Analysis
              </button>
            </div>
          )}
        </div>
      )}

      {/* Past analyses */}
      <div>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">Past Analyses</h2>
        {loading ? (
          <div className="flex justify-center py-8"><LoadingSpinner /></div>
        ) : analyses.length === 0 ? (
          <div className="text-center py-12 text-slate-500">
            <Shield size={36} className="mx-auto mb-3 opacity-30" />
            <p>No analyses yet.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {analyses.map((a) => (
              <button
                key={a.id}
                onClick={() => handleViewAnalysis(a.id)}
                className="w-full text-left bg-slate-900 border border-slate-800 rounded-xl p-4 hover:border-slate-700 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <p className="text-slate-100 font-medium text-sm">{a.subject}</p>
                  <div className="flex items-center gap-2">
                    <Badge variant="blue">{a.analysis_type}</Badge>
                    <span className="text-slate-600 text-xs">{new Date(a.created_at).toLocaleDateString()}</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

    </div>
  );
}
