"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Trash2, ArrowLeft, Pencil, X, Save, CheckCircle } from "lucide-react";
import { api } from "@/lib/api";
import type { Report } from "@/lib/types";
import { countWords, parseWordRange, wordCountColor } from "@/lib/wordCount";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import Badge from "@/components/ui/Badge";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import DownloadMenu from "@/components/ui/DownloadMenu";

const TYPE_LABELS: Record<string, string> = {
  congressional_brief: "Congressional Brief",
  policy_memo: "Policy Memo",
  risk_assessment: "Risk Assessment",
};

const STATUS_OPTIONS = [
  { value: "draft",        label: "Draft" },
  { value: "in_review",    label: "In Review" },
  { value: "pre_approval", label: "Pre-Approval" },
  { value: "completed",    label: "Completed" },
];

interface EditSection {
  key: string;
  title: string;
  instructions: string;
  content: string;
}

/** Split markdown content into structured sections. */
function parseContent(raw: string): { docTitle: string; sections: { title: string; content: string }[] } {
  const sections: { title: string; content: string }[] = [];
  let docTitle = "";
  let cur: { title: string; content: string } | null = null;

  for (const line of raw.split("\n")) {
    if (!docTitle && !cur && line.startsWith("# ")) {
      docTitle = line.slice(2).trim();
    } else if (line.startsWith("## ")) {
      if (cur) { cur.content = cur.content.trim(); sections.push(cur); }
      cur = { title: line.slice(3).trim(), content: "" };
    } else if (line.trim() === "---") {
      // skip dividers
    } else if (cur) {
      cur.content += line + "\n";
    }
  }
  if (cur) { cur.content = cur.content.trim(); sections.push(cur); }

  if (sections.length === 0 && raw.trim()) {
    const body = raw.replace(/^# .+\n/, "").trim();
    if (body) sections.push({ title: "", content: body });
  }
  return { docTitle, sections };
}

/** Reconstruct markdown from edit sections. */
function buildMarkdown(docTitle: string, sections: EditSection[]): string {
  if (sections.length === 1 && !sections[0].title) {
    return `# ${docTitle}\n\n${sections[0].content}`;
  }
  const parts = sections.map((s) =>
    s.title ? `## ${s.title}\n\n${s.content}` : s.content
  );
  return `# ${docTitle}\n\n` + parts.join("\n\n---\n\n");
}

export default function ReportViewPage() {
  const { reportId } = useParams<{ reportId: string }>();
  const router = useRouter();
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);

  // Edit state
  const [editing, setEditing] = useState(false);
  const [loadingTemplate, setLoadingTemplate] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editSections, setEditSections] = useState<EditSection[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [statusSaving, setStatusSaving] = useState(false);

  useEffect(() => {
    api.reports.get(reportId).then((data) => {
      setReport(data as Report);
      setLoading(false);
    });
  }, [reportId]);

  const handleDelete = async () => {
    if (!confirm("Delete this report?")) return;
    await api.reports.delete(reportId);
    router.push("/reports");
  };

  const handleEditStart = async () => {
    if (!report) return;
    setEditing(true);
    setLoadingTemplate(true);
    setSaved(false);

    const { docTitle, sections: parsedSections } = parseContent(report.content || "");
    setEditTitle(docTitle || report.title);

    try {
      // Fetch template for guide text (instructions per section)
      const templateData = await api.reports.template(report.report_type);

      // Map template sections, pre-filling content from existing saved sections
      const matched: EditSection[] = templateData.sections.map((tmpl) => {
        const existing = parsedSections.find(
          (s) => s.title.toLowerCase() === tmpl.title.toLowerCase()
        );
        return {
          key: tmpl.key,
          title: tmpl.title,
          instructions: tmpl.instructions,
          content: existing?.content ?? "",
        };
      });

      // Append any parsed sections not present in template (e.g. manually added)
      for (const ps of parsedSections) {
        const inTemplate = templateData.sections.some(
          (t) => t.title.toLowerCase() === ps.title.toLowerCase()
        );
        if (!inTemplate && ps.title) {
          matched.push({ key: ps.title, title: ps.title, instructions: "", content: ps.content });
        }
      }

      setEditSections(matched);
    } catch {
      // Fallback: use parsed sections without guide text
      setEditSections(
        parsedSections.length > 0
          ? parsedSections.map((s) => ({ key: s.title || "content", title: s.title, instructions: "", content: s.content }))
          : [{ key: "content", title: "", instructions: "", content: "" }]
      );
    } finally {
      setLoadingTemplate(false);
    }
  };

  const handleSave = async () => {
    if (!report) return;
    setSaving(true);
    try {
      const newContent = buildMarkdown(editTitle, editSections);
      await api.reports.update(reportId, { title: editTitle, content: newContent });
      setReport({ ...report, title: editTitle, content: newContent });
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } finally {
      setSaving(false);
    }
  };

  const handleStatusChange = async (newStatus: string) => {
    if (!report) return;
    setStatusSaving(true);
    try {
      await api.reports.update(reportId, { status: newStatus });
      setReport({ ...report, status: newStatus });
    } finally {
      setStatusSaving(false);
    }
  };

  const updateSection = (i: number, content: string) =>
    setEditSections((prev) => prev.map((s, idx) => (idx === i ? { ...s, content } : s)));

  if (loading) return <div className="flex justify-center p-16"><LoadingSpinner /></div>;
  if (!report) return <div className="p-8 text-slate-400">Report not found.</div>;

  return (
    <div className="p-8 max-w-4xl mx-auto">
      {/* ── Header ──────────────────────────────────────────────── */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex-1 min-w-0">
          <button
            onClick={() => router.back()}
            className="flex items-center gap-1.5 text-slate-500 hover:text-slate-300 text-sm mb-3 transition-colors"
          >
            <ArrowLeft size={14} /> Back
          </button>
          <h1 className="text-2xl font-bold text-slate-100 mb-2">{report.title}</h1>
          <div className="flex items-center gap-3 flex-wrap">
            {/* Status selector */}
            <div className="flex items-center gap-1.5">
              <select
                value={report.status}
                onChange={(e) => handleStatusChange(e.target.value)}
                disabled={statusSaving}
                className={[
                  "appearance-none text-xs font-medium px-2 py-0.5 rounded border-0 cursor-pointer",
                  "focus:outline-none focus:ring-1 focus:ring-blue-500 transition-colors",
                  report.status === "draft"        ? "bg-amber-900/50 text-amber-300" : "",
                  report.status === "in_review"    ? "bg-blue-900/50 text-blue-300"   : "",
                  report.status === "pre_approval" ? "bg-purple-900/50 text-purple-300": "",
                  report.status === "completed"    ? "bg-green-900/50 text-green-300"  : "",
                  !["draft","in_review","pre_approval","completed"].includes(report.status)
                                                   ? "bg-slate-700 text-slate-300"     : "",
                ].join(" ")}
              >
                {STATUS_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value} className="bg-slate-800 text-slate-100">
                    {opt.label}
                  </option>
                ))}
              </select>
              {statusSaving && <LoadingSpinner size="sm" />}
            </div>
            <Badge variant="blue">{TYPE_LABELS[report.report_type] || report.report_type}</Badge>
            {report.word_count && (
              <span className="text-slate-500 text-xs">{report.word_count.toLocaleString()} words</span>
            )}
            <span className="text-slate-600 text-xs">
              {new Date(report.created_at).toLocaleDateString("en-US")}
            </span>
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2 ml-4">
          {editing ? (
            <>
              <button
                onClick={handleSave}
                disabled={saving}
                className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50
                           text-white px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
              >
                {saving ? (
                  <><LoadingSpinner size="sm" /> Saving…</>
                ) : saved ? (
                  <><CheckCircle size={13} /> Saved</>
                ) : (
                  <><Save size={13} /> Save</>
                )}
              </button>
              <button
                onClick={() => setEditing(false)}
                className="flex items-center gap-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300
                           px-3 py-1.5 rounded-lg text-sm transition-colors"
              >
                <X size={13} /> Close
              </button>
            </>
          ) : (
            <button
              onClick={handleEditStart}
              className="flex items-center gap-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300
                         px-3 py-1.5 rounded-lg text-sm transition-colors"
            >
              <Pencil size={13} /> Edit
            </button>
          )}
          <DownloadMenu reportId={reportId} variant="button" />
          <button
            onClick={handleDelete}
            className="p-2 text-slate-500 hover:text-red-400 rounded-lg transition-colors"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>

      {/* ── Content area ────────────────────────────────────────── */}
      {editing ? (
        /* Template-style section editor (matches Image #3) */
        loadingTemplate ? (
          <div className="flex justify-center py-16"><LoadingSpinner /></div>
        ) : (
          <div className="space-y-4">
            {/* Editable title */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
              <p className="text-xs text-slate-500 mb-1.5">Report Title</p>
              <input
                type="text"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2.5 text-sm
                           font-semibold text-slate-100 focus:outline-none focus:border-blue-500 transition-colors"
              />
            </div>

            {/* Section cards */}
            {editSections.map((section, i) => {
              const wc = countWords(section.content);
              const range = parseWordRange(section.instructions);
              const colorClass = wordCountColor(wc, range);
              return (
                <div key={section.key || i} className="bg-slate-900 border border-slate-800 rounded-xl p-5">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="w-5 h-5 rounded-full bg-slate-700 text-slate-400 text-xs flex items-center justify-center flex-shrink-0">
                      {i + 1}
                    </span>
                    <h3 className="text-slate-100 font-semibold text-sm">{section.title || "Content"}</h3>
                  </div>
                  {section.instructions && (
                    <p className="text-slate-500 text-xs mb-3 leading-relaxed italic">
                      {section.instructions}
                    </p>
                  )}
                  <textarea
                    value={section.content}
                    onChange={(e) => updateSection(i, e.target.value)}
                    rows={5}
                    placeholder={`Write the ${section.title || "content"} here…`}
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

            {/* Bottom save bar */}
            <div className="flex items-center gap-3 pt-2">
              <button
                onClick={handleSave}
                disabled={saving}
                className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50
                           text-white px-5 py-2.5 rounded-lg text-sm font-medium transition-colors"
              >
                {saving ? (
                  <><LoadingSpinner size="sm" /> Saving…</>
                ) : saved ? (
                  <><CheckCircle size={14} /> Saved</>
                ) : (
                  "Save Report"
                )}
              </button>
              <button
                onClick={() => setEditing(false)}
                className="text-slate-400 hover:text-white text-sm transition-colors"
              >
                Close editor
              </button>
            </div>
          </div>
        )
      ) : (
        /* View mode — rendered Markdown */
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-8">
          {report.content ? (
            <div className="prose prose-invert prose-sm max-w-none prose-a:text-blue-400 prose-hr:border-slate-700">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{report.content}</ReactMarkdown>
            </div>
          ) : (
            <p className="text-slate-500 text-sm">
              No content yet.{" "}
              <button onClick={handleEditStart} className="text-blue-400 hover:underline">
                Start editing →
              </button>
            </p>
          )}
        </div>
      )}
    </div>
  );
}
