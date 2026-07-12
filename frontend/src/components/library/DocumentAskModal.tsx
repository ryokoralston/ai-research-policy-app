"use client";

import { useState } from "react";
import { X } from "lucide-react";
import { api, postStream } from "@/lib/api";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import type { Document, DocumentCitation } from "@/lib/types";

interface DocumentAskModalProps {
  doc: Document;
  onClose: () => void;
}

/**
 * "Ask this document" — single-document Q&A with API-native citations (see
 * backend services/document_qa.py / POST /api/documents/{id}/ask-citations).
 * Distinct from ChatPanel's "Ask Documents", which searches across the
 * whole/selected library via a tool-use loop and assigns its own [N]
 * citations — this sends the one document straight to Claude with
 * `citations: {enabled: true}` and renders whatever spans the API itself
 * locates and quotes.
 */
export default function DocumentAskModal({ doc, onClose }: DocumentAskModalProps) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<DocumentCitation[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [asked, setAsked] = useState(false);

  const handleAsk = async () => {
    if (!question.trim() || running) return;

    setRunning(true);
    setAsked(true);
    setAnswer("");
    setCitations([]);
    setError(null);

    try {
      await postStream(
        api.documents.askCitationsUrl(doc.id),
        { question: question.trim() },
        (event, data) => {
          const d = data as Record<string, unknown>;
          if (event === "token") {
            setAnswer((prev) => prev + (d.text as string));
          } else if (event === "citation") {
            setCitations((prev) => [...prev, d as unknown as DocumentCitation]);
          } else if (event === "complete") {
            setRunning(false);
          } else if (event === "error") {
            setError((d.message as string) || "Something went wrong.");
            setRunning(false);
          }
        }
      );
    } catch {
      setError("Failed to get a response.");
      setRunning(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-slate-900 border border-slate-700 rounded-2xl w-full max-w-2xl max-h-[85vh] flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-slate-800">
          <div className="min-w-0">
            <h3 className="text-slate-100 font-semibold text-sm">Ask this document</h3>
            <p className="text-slate-500 text-xs truncate">{doc.title || doc.filename}</p>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white flex-shrink-0">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {!asked && (
            <p className="text-slate-500 text-sm">
              Ask a question about this document. Claude will quote the exact passages it draws on.
            </p>
          )}

          {asked && (
            <div className="space-y-3">
              <div className="bg-slate-800 rounded-xl px-4 py-3 text-sm text-slate-100 whitespace-pre-wrap leading-relaxed">
                {answer || (running ? "" : "No answer.")}
                {running && (
                  <span className="inline-flex items-center gap-1 mt-1 text-slate-400">
                    <LoadingSpinner size="sm" />
                  </span>
                )}
              </div>

              {error && (
                <p className="text-red-400 text-xs">{error}</p>
              )}

              {citations.length > 0 && (
                <div>
                  <p className="text-slate-400 text-xs font-medium mb-2">Citations</p>
                  <ul className="space-y-2">
                    {citations.map((c) => (
                      <li key={c.index} className="text-xs text-slate-400 flex gap-2">
                        <span className="text-slate-500 flex-shrink-0">[{c.index}]</span>
                        <span>
                          <span className="text-slate-300">&ldquo;{c.cited_text}&rdquo;</span>
                          {c.source_kind === "pdf" && c.start_page_number != null && (
                            <span className="text-slate-500">
                              {" "}
                              — p.{c.start_page_number}
                              {c.end_page_number != null && c.end_page_number !== c.start_page_number
                                ? `–${c.end_page_number}`
                                : ""}
                            </span>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Input */}
        <div className="p-4 border-t border-slate-800">
          <div className="flex gap-2">
            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !running && handleAsk()}
              placeholder="Ask a question about this document..."
              className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500"
              disabled={running}
              autoFocus
            />
            <button
              onClick={handleAsk}
              disabled={running || !question.trim()}
              className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm transition-colors"
            >
              {running ? <LoadingSpinner size="sm" /> : "Ask"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
