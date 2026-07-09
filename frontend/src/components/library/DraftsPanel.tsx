"use client";

import { useState } from "react";
import { FileText, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";

export interface WorkspaceFile {
  name: string;
  size_bytes: number;
  modified_at: string;
}

interface DraftsPanelProps {
  files: WorkspaceFile[];
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

/**
 * Lists files Claude has written to the draft workspace via the text editor
 * tool. Modeled closely on RemindersPanel: same section-header layout, same
 * "render nothing when empty" guard. Clicking a file expands an inline
 * monospace preview fetched on demand (not preloaded for every file).
 */
export default function DraftsPanel({ files }: DraftsPanelProps) {
  const [openFile, setOpenFile] = useState<string | null>(null);
  const [content, setContent] = useState<Record<string, string>>({});
  const [loadingFile, setLoadingFile] = useState<string | null>(null);

  if (files.length === 0) return null;

  const toggleFile = async (name: string) => {
    if (openFile === name) {
      setOpenFile(null);
      return;
    }
    setOpenFile(name);
    if (content[name] === undefined) {
      setLoadingFile(name);
      try {
        const file = await api.workspace.getFile(name);
        setContent((prev) => ({ ...prev, [name]: file.content }));
      } catch {
        setContent((prev) => ({ ...prev, [name]: "Error loading file." }));
      } finally {
        setLoadingFile(null);
      }
    }
  };

  return (
    <div className="border-b border-slate-800 px-4 py-3 space-y-1.5">
      <div className="flex items-center gap-1.5 mb-2">
        <FileText size={12} className="text-emerald-400" />
        <span className="text-xs font-medium text-slate-400">Drafts</span>
      </div>
      {files.map((f) => (
        <div key={f.name} className="bg-slate-800/60 rounded-lg overflow-hidden">
          <button
            onClick={() => toggleFile(f.name)}
            className="w-full flex items-center gap-2 px-3 py-2 text-left"
          >
            {openFile === f.name ? (
              <ChevronDown size={12} className="text-slate-500 flex-shrink-0" />
            ) : (
              <ChevronRight size={12} className="text-slate-500 flex-shrink-0" />
            )}
            <span className="text-xs text-slate-200 truncate flex-1">{f.name}</span>
            <span className="text-xs text-slate-500 flex-shrink-0">{formatSize(f.size_bytes)}</span>
          </button>
          {openFile === f.name && (
            <div className="px-3 pb-2">
              {loadingFile === f.name ? (
                <p className="text-xs text-slate-500">Loading…</p>
              ) : (
                <pre className="text-xs text-slate-300 bg-slate-900/60 rounded-md p-2 max-h-48 overflow-auto whitespace-pre-wrap break-words">
                  {content[f.name] ?? ""}
                </pre>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
