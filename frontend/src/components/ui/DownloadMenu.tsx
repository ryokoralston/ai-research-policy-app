"use client";

import { useState, useRef, useEffect } from "react";
import { Download, ChevronDown, FileText, File } from "lucide-react";
import { api } from "@/lib/api";

interface DownloadMenuProps {
  reportId: string;
  variant?: "icon" | "button";
}

export default function DownloadMenu({ reportId, variant = "button" }: DownloadMenuProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const base = api.reports.exportUrl(reportId);

  return (
    <div ref={ref} className="relative">
      {variant === "button" ? (
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 bg-slate-800 hover:bg-slate-700 text-slate-100 px-3 py-2 rounded-lg text-sm transition-colors"
        >
          <Download size={14} />
          Export
          <ChevronDown size={12} className={`transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
      ) : (
        <button
          onClick={() => setOpen((v) => !v)}
          className="p-2 text-slate-500 hover:text-slate-300 rounded transition-colors"
          title="Download"
        >
          <Download size={15} />
        </button>
      )}

      {open && (
        <div className="absolute right-0 mt-1 w-40 bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-20 overflow-hidden">
          <a
            href={`${base}?format=pdf`}
            download
            onClick={() => setOpen(false)}
            className="flex items-center gap-2.5 px-3 py-2.5 text-sm text-slate-200 hover:bg-slate-700 transition-colors"
          >
            <File size={13} className="text-red-400" />
            PDF
          </a>
          <a
            href={`${base}?format=txt`}
            download
            onClick={() => setOpen(false)}
            className="flex items-center gap-2.5 px-3 py-2.5 text-sm text-slate-200 hover:bg-slate-700 transition-colors"
          >
            <FileText size={13} className="text-slate-400" />
            Plain Text
          </a>
        </div>
      )}
    </div>
  );
}
