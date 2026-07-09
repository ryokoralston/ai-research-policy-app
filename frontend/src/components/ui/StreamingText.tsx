"use client";

import { useEffect, useRef } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation, WebCitation } from "@/lib/types";

/** react-markdown sanitizes link URLs by default and strips any scheme it doesn't
 *  recognize (http/https/mailto/tel/relative) — our synthetic "citation:N" scheme
 *  would otherwise be silently emptied out. Pass it through unchanged; defer to the
 *  library's default sanitization for every other URL so real links stay safe. */
function citationAwareUrlTransform(url: string): string {
  return url.startsWith("citation:") ? url : defaultUrlTransform(url);
}

interface StreamingTextProps {
  text: string;
  className?: string;
  asMarkdown?: boolean;
  /** Sentence-level citations from the Ask Documents RAG chat (see rag_service.py).
   *  When provided, inline [N] markers are rendered as hoverable badges and a
   *  compact "Sources" list is rendered below the answer. */
  citations?: Citation[];
  /** Web-search citations from the Ask Documents RAG chat's web_search tool (see
   *  rag_service.py / anthropic_client.py's extract_web_citations). Not tied to
   *  inline [N] markers — rendered as a separate link-chip list below the
   *  library SourcesList when present. */
  webCitations?: WebCitation[];
}

/** Matches a bracketed citation number not already part of a markdown link, e.g. "[1]" but not "[1](url)". */
const CITATION_MARKER_RE = /\[(\d+)\](?!\()/g;

function CitationTooltip({ citation, children }: { citation: Citation; children: React.ReactNode }) {
  return (
    <span className="group relative inline-block">
      {children}
      <span
        className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1.5 w-64 -translate-x-1/2
                   rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-slate-200
                   opacity-0 shadow-lg transition-opacity duration-150 group-hover:opacity-100"
      >
        <span className="block font-medium text-slate-100">
          {citation.title} <span className="text-slate-400 font-normal">p.{citation.page}</span>
        </span>
        <span className="mt-1 block text-slate-400 line-clamp-3">{citation.snippet}</span>
      </span>
    </span>
  );
}

/** Renders a bracketed citation number, e.g. [1], as a small rounded badge with a hover tooltip. */
function CitationBadge({ citation }: { citation: Citation }) {
  return (
    <CitationTooltip citation={citation}>
      <sup className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full
                       bg-blue-600/30 px-1 text-[10px] font-medium leading-none text-blue-300
                       cursor-default">
        {citation.index}
      </sup>
    </CitationTooltip>
  );
}

/** Custom <a> renderer for react-markdown: intercepts our synthetic "citation:N" links
 *  produced by preprocessing [N] markers, and renders everything else as a normal link. */
function makeCitationLinkRenderer(citations: Citation[]) {
  const byIndex = new Map(citations.map((c) => [c.index, c]));
  return function CitationLink({ href, children }: { href?: string; children?: React.ReactNode }) {
    if (href?.startsWith("citation:")) {
      const n = parseInt(href.slice("citation:".length), 10);
      const citation = byIndex.get(n);
      if (citation) {
        return <CitationBadge citation={citation} />;
      }
      // No matching citation for this number — render the plain text unchanged.
      return <>{children ?? `[${n}]`}</>;
    }
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">
        {children}
      </a>
    );
  };
}

function SourcesList({ citations }: { citations: Citation[] }) {
  return (
    <div className="mt-2 flex flex-wrap gap-1.5 border-t border-slate-700/60 pt-2">
      <span className="text-[10px] uppercase tracking-wide text-slate-500 w-full">Sources</span>
      {citations
        .slice()
        .sort((a, b) => a.index - b.index)
        .map((c) => (
          <CitationTooltip key={c.chunk_id} citation={c}>
            <span
              className="inline-flex items-center gap-1 rounded-full bg-slate-700/60 px-2 py-0.5
                         text-[11px] text-slate-300 cursor-default hover:bg-slate-700"
            >
              <span className="text-blue-300">[{c.index}]</span>
              {c.title}, p.{c.page}
            </span>
          </CitationTooltip>
        ))}
    </div>
  );
}

/** Web-search source list — link chips styled like SourcesList, but each chip
 *  is a real link (target="_blank") since there's no [N] marker in the text
 *  pointing back to it. */
function WebSourcesList({ citations }: { citations: WebCitation[] }) {
  return (
    <div className="mt-2 flex flex-wrap gap-1.5 border-t border-slate-700/60 pt-2">
      <span className="text-[10px] uppercase tracking-wide text-slate-500 w-full">Web sources</span>
      {citations.map((c) => (
        <a
          key={c.url}
          href={c.url}
          target="_blank"
          rel="noopener noreferrer"
          title={c.cited_text}
          className="inline-flex items-center gap-1 rounded-full bg-slate-700/60 px-2 py-0.5
                     text-[11px] text-slate-300 hover:bg-slate-700 hover:text-blue-300"
        >
          {c.title}
        </a>
      ))}
    </div>
  );
}

export default function StreamingText({ text, className = "", asMarkdown = true, citations, webCitations }: StreamingTextProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [text]);

  if (!asMarkdown) {
    return <span className={className}>{text}</span>;
  }

  const hasCitations = !!citations && citations.length > 0;
  const renderedText = hasCitations
    ? text.replace(CITATION_MARKER_RE, (_match, n) => `[${n}](citation:${n})`)
    : text;

  return (
    <div className={`prose prose-invert prose-sm max-w-none ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={hasCitations ? { a: makeCitationLinkRenderer(citations!) } : undefined}
        urlTransform={hasCitations ? citationAwareUrlTransform : undefined}
      >
        {renderedText}
      </ReactMarkdown>
      {hasCitations && <SourcesList citations={citations!} />}
      {!!webCitations && webCitations.length > 0 && <WebSourcesList citations={webCitations} />}
      <div ref={endRef} />
    </div>
  );
}
