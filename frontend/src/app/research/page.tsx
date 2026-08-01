"use client";

import { Suspense, useState, useRef, useEffect, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ExternalLink, FileText, FolderPlus, ArrowUp, Square } from "lucide-react";
import { api, authFetch, consumeSseStream } from "@/lib/api";
import StreamingText from "@/components/ui/StreamingText";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import ModelPicker, { type ModelOption } from "@/components/ui/ModelPicker";
import { RESEARCH_UPDATED_EVENT } from "@/components/layout/Sidebar";

type Phase = "idle" | "searching" | "summarizing" | "synthesizing" | "done" | "error";

interface SourceCard {
  order: number;
  title: string;
  url: string;
  snippet?: string;
  ai_summary?: string;
}

export default function ResearchPage() {
  // useSearchParams needs a Suspense boundary in an otherwise static page.
  return (
    <Suspense fallback={null}>
      <ResearchAgent />
    </Suspense>
  );
}

function ResearchAgent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const sessionParam = searchParams.get("session");
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
  const [models, setModels] = useState<ModelOption[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    // Session-only override for the synthesis step (see ModelPicker) —
    // seeded from the account's default main model, but never written back
    // to Settings. Falls back silently if the catalog fetch fails.
    Promise.all([api.settings.getAvailableModels(), api.settings.getModels()])
      .then(([catalog, settings]) => {
        setModels(catalog.models);
        setSelectedModel(settings.main_model);
      })
      .catch(() => {});
  }, []);

  // Tells the sidebar's Recent list to refetch (see RESEARCH_UPDATED_EVENT).
  const notifyRecentChanged = useCallback(() => {
    window.dispatchEvent(new Event(RESEARCH_UPDATED_EVENT));
  }, []);

  // Open whichever session the URL names — that's how the sidebar's Recent
  // list hands a session to this page.
  useEffect(() => {
    if (!sessionParam) return;
    let cancelled = false;
    // A run already in flight would keep streaming into the view we're about
    // to replace, so stop it first.
    abortRef.current?.abort();
    setError(null);
    setSavedToLibrary(false);
    setSessionId(sessionParam);
    (async () => {
      try {
        const detail = await api.research.get(sessionParam);
        if (cancelled) return;
        setSources(
          (detail.results || []).map((r) => ({
            order: r.result_order,
            title: r.title || "Untitled",
            url: r.url,
            snippet: r.snippet ?? undefined,
            ai_summary: r.ai_summary ?? undefined,
          }))
        );
        setSynthesis(detail.summary || "");
        setPhase(detail.status === "error" ? "error" : "done");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load session");
        setPhase("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionParam]);

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
        body: JSON.stringify({
          query,
          max_sources: maxSources,
          model: selectedModel || undefined,
        }),
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
      await consumeSseStream(streamRes.body, (event, raw) => {
        const d = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
        if (event === "status") {
          setStatusMsg(d.message as string);
        } else if (event === "queries") {
          setStatusMsg(`Searching with ${(d.queries as string[]).length} queries...`);
          setPhase("searching");
        } else if (event === "sources_found") {
          setStatusMsg(`Found ${d.count} sources. Summarizing...`);
          setPhase("summarizing");
        } else if (event === "source_processed") {
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
        } else if (event === "synthesis_token") {
          setPhase("synthesizing");
          setStatusMsg("Synthesizing findings...");
          setSynthesis((prev) => prev + (d.text as string));
        } else if (event === "resynthesis_start") {
          // A brand-new synthesis is about to stream, replacing the previous
          // one — reset so synthesis_token doesn't concatenate old + new text.
          setSynthesis("");
          setPhase("synthesizing");
          setStatusMsg("Found evidence gaps — researching further...");
        } else if (event === "gap_queries") {
          const gapQueries = (d.queries as string[]) || [];
          setStatusMsg(`Researching ${gapQueries.length} evidence gap(s)...`);
          setPhase("searching");
        } else if (event === "gaps_closed") {
          // No-op for now: the next event (either another resynthesis_start
          // or complete) will update phase/status. Nothing to show here.
        } else if (event === "complete") {
          setPhase("done");
          setStatusMsg("Research complete");
          notifyRecentChanged();
        } else if (event === "error") {
          setError(d.message as string);
          setPhase("error");
          notifyRecentChanged();
        }
      });
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
    <div className="p-8 max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-100 mb-1">Research Agent</h1>
        <p className="text-slate-400 text-sm">
          Enter a policy question. The agent will search the web, summarize sources, and synthesize findings.
        </p>
      </div>

      {/* Search Form — composer layout: the query field sits above a control row
          holding source count and the model picker. Enter submits; there is no
          submit button, so the Stop button below is the only control that appears
          in the row's right slot (while a run is in flight). */}
      {/* Capped well short of the page width: stretched across the full 7xl
          container the field read as an oversized banner rather than an input. */}
      <form onSubmit={handleSubmit} className="mb-8 max-w-4xl">
        <div className="bg-slate-900 border border-slate-700 rounded-2xl px-3 py-2 focus-within:border-blue-500 transition-colors">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              // isComposing guard: while an IME is mid-conversion (Japanese, etc.)
              // Enter commits the candidate — submitting there would fire on a
              // half-typed query.
              if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                e.preventDefault();
                handleSubmit(e);
              }
            }}
            placeholder="e.g. What are the main AI governance risks from autonomous weapons systems?"
            // Labels the Enter key as "Send" on mobile keyboards instead of the
            // generic return glyph.
            enterKeyHint="send"
            className="w-full bg-transparent px-1 py-1 text-sm text-slate-100 placeholder-slate-500 focus:outline-none disabled:opacity-50"
            disabled={isRunning}
          />
          <div className="flex items-center justify-end gap-3 mt-1.5">
            {/* Source count and model sit together, right-aligned: both decide how
                much a run costs and how deep it goes, so proximity should read
                them as one set (the same pairing Claude's composer uses for model
                + effort). The left slot is left free — that's where an attachment
                or tools menu goes in this pattern, and we have none. */}
            <div className="flex items-center gap-1">
              {/* bg-slate-900 matches the composer surface (so the control reads as
                  flat) while still giving the native option list a real background. */}
              <select
                value={maxSources}
                onChange={(e) => setMaxSources(Number(e.target.value))}
                className="bg-slate-900 rounded-lg px-2 py-1.5 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-100 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                disabled={isRunning}
              >
                <option value={5}>5 sources</option>
                <option value={8}>8 sources</option>
                <option value={12}>12 sources</option>
              </select>
              {models.length > 0 && (
                <ModelPicker
                  models={models}
                  value={selectedModel}
                  onChange={setSelectedModel}
                  disabled={isRunning}
                />
              )}
            </div>
            {/* One slot that swaps role: send while idle, stop while running.
                Enter submits too, but the button is what makes the action
                reachable on touch and to screen readers — Enter alone is not a
                dependable submit path on mobile or with an IME. */}
            {isRunning ? (
              <button
                type="button"
                onClick={handleStop}
                aria-label="Stop research"
                className="flex items-center justify-center w-8 h-8 rounded-full bg-red-600 hover:bg-red-700 text-white transition-colors"
              >
                <Square size={13} fill="currentColor" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!query.trim()}
                aria-label="Start research"
                // Dim via opacity rather than swapping to a slate background:
                // globals.css only re-maps the base color classes for the light
                // theme, so a `disabled:bg-*` would keep Tailwind's dark default
                // and the empty state would look active. Opacity is theme-agnostic.
                className="flex items-center justify-center w-8 h-8 rounded-full bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-30 disabled:hover:bg-blue-600 disabled:cursor-not-allowed transition-all"
              >
                <ArrowUp size={16} />
              </button>
            )}
          </div>
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
