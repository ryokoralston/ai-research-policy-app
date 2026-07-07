"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Trash2, File, FileText, Download, ChevronDown } from "lucide-react";
import { api, downloadFile } from "@/lib/api";
import type { RiskAnalysis, CitationConfidence } from "@/lib/types";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Badge from "@/components/ui/Badge";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import CitationConfidenceCard from "@/components/ui/CitationConfidenceCard";

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
        <span className="text-slate-100 font-mono font-semibold">{score}/10</span>
      </div>
      <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${score * 10}%` }} />
      </div>
    </div>
  );
}

function ExportMenu({ analysisId }: { analysisId: string }) {
  const [open, setOpen] = useState(false);
  const base = api.analysis.exportUrl(analysisId);

  const handleDownload = (format: "pdf" | "txt") => {
    setOpen(false);
    downloadFile(`${base}?format=${format}`, `analysis-${analysisId.slice(0, 8)}.${format}`).catch(
      (err) => console.error("Export failed:", err)
    );
  };

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const el = document.getElementById("analysis-export-menu");
      if (el && !el.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div id="analysis-export-menu" className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 bg-slate-800 hover:bg-slate-700 text-slate-100 px-3 py-2 rounded-lg text-sm transition-colors"
      >
        <Download size={14} />
        Export
        <ChevronDown size={12} className={`transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-40 bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-20 overflow-hidden">
          <button
            onClick={() => handleDownload("pdf")}
            className="flex w-full items-center gap-2.5 px-3 py-2.5 text-sm text-slate-200 hover:bg-slate-700 transition-colors"
          >
            <File size={13} className="text-red-400" />
            PDF
          </button>
          <button
            onClick={() => handleDownload("txt")}
            className="flex w-full items-center gap-2.5 px-3 py-2.5 text-sm text-slate-200 hover:bg-slate-700 transition-colors"
          >
            <FileText size={13} className="text-slate-400" />
            Plain Text
          </button>
        </div>
      )}
    </div>
  );
}

export default function AnalysisDetailPage() {
  const { analysisId } = useParams<{ analysisId: string }>();
  const router = useRouter();
  const [analysis, setAnalysis] = useState<RiskAnalysis | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.analysis.get(analysisId).then((data) => {
      setAnalysis(data);
      setLoading(false);
    });
  }, [analysisId]);

  const handleDelete = async () => {
    if (!confirm("Delete this analysis?")) return;
    await api.analysis.delete(analysisId);
    router.push("/analysis");
  };

  if (loading) return <div className="flex justify-center p-16"><LoadingSpinner /></div>;
  if (!analysis) return <div className="p-8 text-slate-400">Analysis not found.</div>;

  const scores = analysis.risk_scores_json
    ? (JSON.parse(analysis.risk_scores_json) as Record<string, number>)
    : null;
  const citationConfidence = analysis.citation_confidence_json
    ? (JSON.parse(analysis.citation_confidence_json) as CitationConfidence)
    : null;

  const TYPE_LABELS: Record<string, string> = {
    technology: "Technology",
    policy: "Policy",
    actor: "Actor/Organization",
  };

  return (
    <div className="p-8 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex-1">
          <button
            onClick={() => router.back()}
            className="flex items-center gap-1.5 text-slate-500 hover:text-slate-300 text-sm mb-3 transition-colors"
          >
            <ArrowLeft size={14} /> Back
          </button>
          <h1 className="text-2xl font-bold text-slate-100 mb-2">{analysis.subject}</h1>
          <div className="flex items-center gap-3">
            <Badge variant="blue">
              {TYPE_LABELS[analysis.analysis_type] || analysis.analysis_type}
            </Badge>
            <Badge variant="green">Risk Analysis</Badge>
            <span className="text-slate-600 text-xs">
              {new Date(analysis.created_at).toLocaleDateString("en-US")}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2 ml-4">
          <ExportMenu analysisId={analysisId} />
          <button
            onClick={handleDelete}
            className="p-2 text-slate-500 hover:text-red-400 rounded-lg transition-colors"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>

      {/* Risk Scores */}
      {scores && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 mb-6">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">
            Risk Scores
          </h2>
          <div className="grid grid-cols-2 gap-4">
            {Object.entries(scores).map(([key, val]) => (
              <ScoreBar key={key} label={SCORE_LABELS[key] || key} score={val} />
            ))}
          </div>
        </div>
      )}

      {/* Citation confidence */}
      <CitationConfidenceCard confidence={citationConfidence} />

      {/* Content */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-8">
        {analysis.content ? (
          <div className="prose prose-invert prose-sm max-w-none prose-a:text-blue-400 prose-hr:border-slate-700">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{analysis.content}</ReactMarkdown>
          </div>
        ) : (
          <p className="text-slate-500 text-sm">No content available.</p>
        )}
      </div>
    </div>
  );
}
