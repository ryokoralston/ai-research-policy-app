"use client";

import { useState } from "react";
import { Upload, RefreshCw, Link } from "lucide-react";
import { api } from "@/lib/api";
import LoadingSpinner from "@/components/ui/LoadingSpinner";

interface UploadPanelProps {
  /** Called after a successful upload or URL ingestion so the parent can refresh the doc list. */
  onUploaded: () => void;
}

/**
 * G-1: extracted from app/library/page.tsx (upload + URL/YouTube ingestion +
 * drag-and-drop). JSX and className are unchanged from the original —
 * mechanical extraction only.
 */
export default function UploadPanel({ onUploaded }: UploadPanelProps) {
  const [urlInput, setUrlInput] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);

  const handleUpload = async (files: File[]) => {
    const invalid = files.filter(
      (f) => !["pdf", "txt", "html", "htm", "png", "jpg", "jpeg", "webp", "gif"].includes(
        f.name.split(".").pop()?.toLowerCase() ?? ""
      )
    );
    if (invalid.length > 0) {
      alert(
        `Unsupported file type: ${invalid.map((f) => f.name).join(", ")}\n` +
          "Only PDF, TXT, HTML, and image files (PNG, JPG, WEBP, GIF) are supported."
      );
      return;
    }
    setUploading(true);
    try {
      await Promise.all(files.map((f) => api.documents.upload(f)));
      onUploaded();
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length > 0) handleUpload(Array.from(e.dataTransfer.files));
  };

  const handleIngestUrl = async () => {
    if (!urlInput.trim()) return;
    setIngesting(true);
    setIngestError(null);
    try {
      await api.documents.ingestUrl(urlInput.trim());
      setUrlInput("");
      onUploaded();
    } catch (err) {
      setIngestError(err instanceof Error ? err.message : "Failed to add URL");
    } finally {
      setIngesting(false);
    }
  };

  return (
    <>
      {/* URL / YouTube ingestion */}
      <div className="mb-4">
        <div className="flex gap-2">
          <input
            type="url"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleIngestUrl()}
            placeholder="Paste a web page URL or YouTube link..."
            disabled={ingesting}
            className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100
                       placeholder-slate-500 focus:outline-none focus:border-blue-500 transition-colors"
          />
          <button
            onClick={handleIngestUrl}
            disabled={ingesting || !urlInput.trim()}
            className="flex items-center gap-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50
                       disabled:cursor-not-allowed text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          >
            {ingesting ? <RefreshCw size={14} className="animate-spin" /> : <Link size={14} />}
            {ingesting ? "Adding..." : "Add URL"}
          </button>
        </div>
        {ingestError && <p className="mt-2 text-xs text-red-400">{ingestError}</p>}
      </div>

      {/* File upload zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={`border-2 border-dashed rounded-xl p-6 text-center mb-8 transition-colors ${
          dragging ? "border-blue-500 bg-blue-900/10" : "border-slate-700 hover:border-slate-600"
        }`}
      >
        <Upload size={22} className="mx-auto mb-2 text-slate-500" />
        <p className="text-slate-400 text-sm mb-1">
          Drag & drop files here, or{" "}
          <label className="text-blue-400 hover:underline cursor-pointer">
            browse
            <input
              type="file"
              accept=".pdf,.txt,.html,.htm,.png,.jpg,.jpeg,.webp,.gif"
              multiple
              style={{ display: "none" }}
              onChange={(e) =>
                e.target.files && e.target.files.length > 0 && handleUpload(Array.from(e.target.files))
              }
            />
          </label>
        </p>
        <p className="text-slate-600 text-xs">PDF, TXT, HTML, images (PNG/JPG/WEBP/GIF) — multiple files supported</p>
        {uploading && <div className="mt-2"><LoadingSpinner size="sm" /></div>}
      </div>
    </>
  );
}
