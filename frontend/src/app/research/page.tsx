"use client";

import { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { Search, ExternalLink, FileText, FolderPlus } from "lucide-react";
import { api, authFetch } from "@/lib/api";
import StreamingText from "@/components/ui/StreamingText";
import LoadingSpinner from "@/components/ui/LoadingSpinner";

type Phase = "idle" | "searching" | "summarizing" | "synthesizing" | "done" | "error";

interface SourceCard {
  order: number;
  title: string;
  url: string;
  snippet?: string;
  ai_summary?: string;
}

export default function ResearchPage() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [maxSources, setMaxSources] = useState(8);
  const [phase, setPhase] = useState<Phase>("idle");
  const [statusMsg, setStatusMsg] = useState("");
  const [sources, setSources] = useState<SourceCard[]>([]);
  const [synthesis, setSynthesis] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedToLibrary, setSavedToLibrary] = useState(false);
  const [saving, setSaving] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;

    // Reset state
    setPhase("searching");
    setStatusMsg("Starting research...");
    setSources([]);
    setSynthesis("");
    setSessionId(null);
    setError(null);
    setSavedToLibrary(false);

    abortRef.current = new AbortController();

    try {
      // Start research session
      const res = await authFetch(api.research.startUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, max_sources: maxSources }),
        signal: abortRef.current.signal,
      });
      const { session_id } = await res.json();
      setSessionId(session_id);

      // Stream the SSE results (GET endpoint)
      const streamRes = await authFetch(
        api.research.streamUrl(session_id),
        { signal: abortRef.current.signal }
      );
      if (!streamRes.ok || !streamRes.body) {
        throw new Error(`Stream request failed: ${streamRes.status}`);
      }
      const reader = streamRes.body.getReader();
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
              let d: Record<string, unknown> = {};
              try { d = JSON.parse(currentData); } catch { /* ignore */ }
              if (currentEvent === "status") {
                setStatusMsg(d.message as string);
              } else if (currentEvent === "queries") {
                setStatusMsg(`Searching with ${(d.queries as string[]).length} queries...`);
                setPhase("searching");
              } else if (currentEvent === "sources_found") {
                setStatusMsg(`Found ${d.count} sources. Summarizing...`);
                setPhase("summarizing");
              } else if (currentEvent === "source_processed") {
                setSources((prev) => [
                  ...prev,
                  {
                    order: d.order as number,
                    title: (d.title as string) || "Untitled",
                    url: d.url as string,
                    snippet: d.snippet as string | undefined,
                    ai_summary: d.ai_summary as string | undefined,
                  },
                ]);
              } else if (currentEvent === "synthesis_token") {
                setPhase("synthesizing");
                setStatusMsg("Synthesizing findings...");
                setSynthesis((prev) => prev + (d.text as string));
              } else if (currentEvent === "complete") {
                setPhase("done");
                setStatusMsg("Research complete");
              } else if (currentEvent === "error") {
                setError(d.message as string);
                setPhase("error");
              }
              currentEvent = "message";
              currentData = "";
            }
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        setError(err.message);
        setPhase("error");
      }
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
    setPhase("done");
  };

  const handleSaveToLibrary = async () => {
    if (!sessionId) return;
    setSaving(true);
    try {
      await api.research.saveToLibrary(sessionId);
      setSavedToLibrary(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save to library");
    } finally {
      setSaving(false);
    }
  };

  const isRunning = phase === "searching" || phase === "summarizing" || phase === "synthesizing";

  return (
    <div className="p-8 max-w-6xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-100 mb-1">Research Agent</h1>
        <p className="text-slate-400 text-sm">
          Enter a policy question. The agent will search the web, summarize sources, and synthesize findings.
        </p>
      </div>

      {/* Search Form */}
      <form onSubmit={handleSubmit} className="mb-8">
        <div className="flex gap-3">
          <div className="flex-1 relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. What are the main AI governance risks from autonomous weapons systems?"
              className="w-full bg-slate-900 border border-slate-700 rounded-lg pl-9 pr-4 py-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500"
              disabled={isRunning}
            />
          </div>
          <select
            value={maxSources}
            onChange={(e) => setMaxSources(Number(e.target.value))}
            className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-3 text-sm text-slate-300 focus:outline-none focus:border-blue-500"
            disabled={isRunning}
          >
            <option value={5}>5 sources</option>
            <option value={8}>8 sources</option>
            <option value={12}>12 sources</option>
          </select>
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
              disabled={!query.trim()}
              className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-5 py-3 rounded-lg text-sm font-medium transition-colors"
            >
              Research
            </button>
          )}
        </div>
      </form>

      {/* Error */}
      {error && (
        <div className="mb-6 bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Running status */}
      {isRunning && (
        <div className="flex items-center gap-3 mb-6 text-slate-400 text-sm">
          <LoadingSpinner size="sm" />
          <span>{statusMsg}</span>
        </div>
      )}

      {/* Two-column layout when we have results */}
      {(sources.length > 0 || synthesis) && (
        <div className="grid grid-cols-5 gap-6">
          {/* Sources column */}
          <div className="col-span-2 space-y-3">
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
              Sources ({sources.length})
            </h2>
            {sources.map((src) => (
              <div
                key={src.order}
                className="bg-slate-900 border border-slate-800 rounded-lg p-4"
              >
                <div className="flex items-start justify-between gap-2 mb-2">
                  <p className="text-slate-100 text-sm font-medium leading-tight line-clamp-2">
                    {src.title}
                  </p>
                  <a
                    href={src.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex-shrink-0 text-slate-500 hover:text-slate-300"
                  >
                    <ExternalLink size={13} />
                  </a>
                </div>
                {src.ai_summary && (
                  <p className="text-slate-400 text-xs leading-relaxed">{src.ai_summary}</p>
                )}
              </div>
            ))}
          </div>

          {/* Synthesis column */}
          <div className="col-span-3">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
                Synthesis
              </h2>
              {phase === "done" && sessionId && (
                <div className="flex items-center gap-2">
                  {savedToLibrary ? (
                    <span className="text-xs text-green-400 flex items-center gap-1">
                      <FolderPlus size={12} />
                      Saved to Library
                    </span>
                  ) : (
                    <button
                      onClick={handleSaveToLibrary}
                      disabled={saving}
                      className="flex items-center gap-1.5 text-xs bg-slate-700/50 text-slate-300 hover:bg-slate-700 px-3 py-1.5 rounded-md transition-colors disabled:opacity-50"
                    >
                      <FolderPlus size={12} />
                      {saving ? "Saving..." : "Save to Library"}
                    </button>
                  )}
                  <button
                    onClick={() =>
                      router.push(`/reports/new?session_id=${sessionId}`)
                    }
                    className="flex items-center gap-1.5 text-xs bg-blue-600/20 text-blue-400 hover:bg-blue-600/30 px-3 py-1.5 rounded-md transition-colors"
                  >
                    <FileText size={12} />
                    Generate Report
                  </button>
                </div>
              )}
            </div>
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-5 min-h-48">
              {synthesis ? (
                <StreamingText text={synthesis} />
              ) : (
                <div className="flex items-center justify-center h-32 text-slate-600 text-sm">
                  {isRunning ? "Synthesizing findings..." : ""}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
