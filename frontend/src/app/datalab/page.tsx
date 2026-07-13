"use client";

import { useRef, useState } from "react";
import { FlaskConical, Upload, AlertCircle } from "lucide-react";
import { api, postStreamForm } from "@/lib/api";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import StreamingText from "@/components/ui/StreamingText";

const ACCEPTED_EXTENSIONS = [".csv", ".json", ".xlsx", ".txt"];

type Segment =
  | { kind: "text"; content: string }
  | { kind: "code"; content: string }
  | { kind: "stdout"; content: string; returnCode: number | null }
  | { kind: "image"; filename: string; mediaType: string; dataBase64: string };

function CodeBlock({ code }: { code: string }) {
  return (
    <details className="my-3 bg-slate-950 border border-slate-800 rounded-lg text-xs" open>
      <summary className="cursor-pointer select-none px-3 py-2 text-slate-400 hover:text-slate-200 transition-colors">
        Code
      </summary>
      <pre className="px-3 pb-3 pt-1 overflow-x-auto border-t border-slate-800/70">
        <code className="font-mono text-slate-300 whitespace-pre">{code}</code>
      </pre>
    </details>
  );
}

function StdoutBlock({ content, returnCode }: { content: string; returnCode: number | null }) {
  const failed = returnCode !== null && returnCode !== 0;
  return (
    <div
      className={`my-3 rounded-lg border px-3 py-2 text-xs font-mono whitespace-pre-wrap ${
        failed ? "bg-red-950/30 border-red-900 text-red-300" : "bg-slate-900/60 border-slate-800 text-slate-500"
      }`}
    >
      {content || <span className="italic opacity-60">(no output)</span>}
      {returnCode !== null && returnCode !== 0 && (
        <div className="mt-1 text-red-400 font-sans">exit code {returnCode}</div>
      )}
    </div>
  );
}

export default function DataLabPage() {
  const [file, setFile] = useState<File | null>(null);
  const [question, setQuestion] = useState("");
  const [segments, setSegments] = useState<Segment[]>([]);
  const [notes, setNotes] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [codeRuns, setCodeRuns] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const appendToken = (text: string) => {
    setSegments((prev) => {
      const last = prev[prev.length - 1];
      if (last && last.kind === "text") {
        return [...prev.slice(0, -1), { kind: "text", content: last.content + text }];
      }
      return [...prev, { kind: "text", content: text }];
    });
  };

  const handleSubmit = async () => {
    if (!file || !question.trim() || running) return;
    setRunning(true);
    setError(null);
    setSegments([]);
    setNotes([]);
    setCodeRuns(null);
    abortRef.current = new AbortController();

    const form = new FormData();
    form.append("file", file);
    form.append("question", question);

    try {
      await postStreamForm(
        api.datalab.analyzeUrl(),
        form,
        (event, data) => {
          const d = data as Record<string, unknown>;
          if (event === "token") {
            appendToken(d.text as string);
          } else if (event === "code") {
            setSegments((prev) => [...prev, { kind: "code", content: d.code as string }]);
          } else if (event === "stdout") {
            setSegments((prev) => [
              ...prev,
              { kind: "stdout", content: d.text as string, returnCode: (d.return_code as number | null) ?? null },
            ]);
          } else if (event === "image") {
            setSegments((prev) => [
              ...prev,
              {
                kind: "image",
                filename: d.filename as string,
                mediaType: d.media_type as string,
                dataBase64: d.data_base64 as string,
              },
            ]);
          } else if (event === "note") {
            setNotes((prev) => [...prev, d.message as string]);
          } else if (event === "complete") {
            setCodeRuns(d.code_runs as number);
            setRunning(false);
          } else if (event === "error") {
            setError(d.message as string);
            setRunning(false);
          }
        },
        abortRef.current.signal
      );
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        setError(err.message);
      }
      setRunning(false);
    }
  };

  const hasOutput = segments.length > 0 || running;

  return (
    <div className="p-8 max-w-5xl mx-auto">
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-1">
          <FlaskConical size={22} className="text-blue-400" />
          <h1 className="text-2xl font-bold text-slate-100">Data Lab</h1>
        </div>
        <p className="text-slate-400 text-sm">
          Upload a data file and ask Claude to analyze it — code runs in a sandboxed environment and any
          charts it produces are shown inline.
        </p>
      </div>

      <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 mb-8 space-y-4">
        <div>
          <label className="block text-sm text-slate-400 mb-2">Data file (CSV, JSON, XLSX, or TXT — max 10MB)</label>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="w-full flex items-center gap-3 bg-slate-800 border border-dashed border-slate-700 rounded-lg px-4 py-3 text-sm text-slate-300 hover:border-blue-500 transition-colors"
          >
            <Upload size={16} className="flex-shrink-0 text-slate-500" />
            {file ? (
              <span className="truncate">{file.name} ({(file.size / 1024).toFixed(1)} KB)</span>
            ) : (
              <span className="text-slate-500">Click to choose a file…</span>
            )}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPTED_EXTENSIONS.join(",")}
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>

        <div>
          <label className="block text-sm text-slate-400 mb-2">Question</label>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="What are the main drivers of churn?"
            rows={3}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500 resize-none"
          />
        </div>

        <button
          onClick={handleSubmit}
          disabled={!file || !question.trim() || running}
          className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-6 py-2.5 rounded-lg text-sm font-medium transition-colors"
        >
          {running ? "Analyzing…" : "Run Analysis"}
        </button>
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-800 rounded-lg p-4 text-red-300 text-sm mb-6 flex items-start gap-2">
          <AlertCircle size={16} className="flex-shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {hasOutput && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-6">
          {running && (
            <div className="flex items-center gap-2 text-sm text-slate-400 mb-4">
              <LoadingSpinner size="sm" />
              <span>Running analysis — this can take a few minutes…</span>
            </div>
          )}

          {segments.map((seg, i) => {
            if (seg.kind === "text") {
              return <StreamingText key={i} text={seg.content} />;
            }
            if (seg.kind === "code") {
              return <CodeBlock key={i} code={seg.content} />;
            }
            if (seg.kind === "stdout") {
              return <StdoutBlock key={i} content={seg.content} returnCode={seg.returnCode} />;
            }
            return (
              <img
                key={i}
                src={`data:${seg.mediaType};base64,${seg.dataBase64}`}
                alt={seg.filename}
                className="my-4 max-w-full rounded-lg border border-slate-800"
              />
            );
          })}

          {notes.map((note, i) => (
            <p key={i} className="mt-3 text-xs text-amber-400/80 italic">
              {note}
            </p>
          ))}

          {!running && codeRuns !== null && (
            <p className="mt-4 pt-4 border-t border-slate-800 text-xs text-slate-500">
              {codeRuns} code execution{codeRuns === 1 ? "" : "s"}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
