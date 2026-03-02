"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Plus, FileText, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { Report } from "@/lib/types";
import Badge from "@/components/ui/Badge";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import DownloadMenu from "@/components/ui/DownloadMenu";

const TYPE_LABELS: Record<string, string> = {
  congressional_brief: "Congressional Brief",
  policy_memo: "Policy Memo",
  risk_assessment: "Risk Assessment",
};

const STATUS_LABELS: Record<string, string> = {
  draft:        "Draft",
  in_review:    "In Review",
  pre_approval: "Pre-Approval",
  completed:    "Completed",
};

export default function ReportsPage() {
  const [reports, setReports] = useState<Report[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.reports.list().then((data) => {
      setReports(data as Report[]);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this report?")) return;
    await api.reports.delete(id);
    setReports((prev) => prev.filter((r) => r.id !== id));
  };

  return (
    <div className="p-8 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-slate-100 mb-1">Reports</h1>
          <p className="text-slate-400 text-sm">Generated policy reports and briefings</p>
        </div>
        <Link
          href="/reports/new"
          className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        >
          <Plus size={16} />
          New Report
        </Link>
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <LoadingSpinner />
        </div>
      ) : reports.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <FileText size={40} className="mx-auto mb-3 opacity-30" />
          <p>No reports yet. Generate one from a research session.</p>
          <Link href="/reports/new" className="text-blue-400 hover:underline text-sm mt-2 inline-block">
            Create a report →
          </Link>
        </div>
      ) : (
        <div className="space-y-3">
          {reports.map((report) => (
            <div
              key={report.id}
              className="bg-slate-900 border border-slate-800 rounded-xl p-5 flex items-start gap-4 hover:border-slate-700 transition-colors"
            >
              <div className="flex-1 min-w-0">
                <Link href={`/reports/${report.id}`} className="block">
                  <h3 className="text-slate-100 font-semibold hover:text-blue-400 transition-colors truncate">
                    {report.title}
                  </h3>
                </Link>
                <div className="flex items-center gap-3 mt-1.5">
                  <Badge variant={
                    report.status === "completed"    ? "green" :
                    report.status === "pre_approval" ? "amber" :
                    report.status === "in_review"    ? "blue"  :
                    report.status === "draft"        ? "amber" : "default"
                  }>
                    {STATUS_LABELS[report.status] ?? report.status}
                  </Badge>
                  <Badge variant="blue">{TYPE_LABELS[report.report_type] || report.report_type}</Badge>
                  {report.word_count && (
                    <span className="text-slate-500 text-xs">{report.word_count.toLocaleString()} words</span>
                  )}
                  <span className="text-slate-600 text-xs">
                    {new Date(report.created_at).toLocaleDateString()}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <DownloadMenu reportId={report.id} variant="icon" />
                <button
                  onClick={() => handleDelete(report.id)}
                  className="p-2 text-slate-500 hover:text-red-400 rounded transition-colors"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
