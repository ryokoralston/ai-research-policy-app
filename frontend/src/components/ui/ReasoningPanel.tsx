/**
 * Collapsible panel for the model's streamed "thinking" text (adaptive
 * thinking — see backend services/anthropic_client.py::stream_text_with_thinking).
 *
 * Collapsed by default via <details>; renders nothing when there's no
 * accumulated thinking text, so streams that never emit a "thinking" SSE
 * event (older cached responses, models without thinking support) show
 * nothing extra.
 */
export default function ReasoningPanel({ text }: { text: string }) {
  if (!text.trim()) return null;

  return (
    <details className="mb-3 bg-slate-900/50 border border-slate-800 rounded-lg text-xs">
      <summary className="cursor-pointer select-none px-3 py-2 text-slate-500 hover:text-slate-400 transition-colors">
        Reasoning
      </summary>
      <div className="px-3 pb-3 pt-1 whitespace-pre-wrap text-slate-500 border-t border-slate-800/70 leading-relaxed">
        {text}
      </div>
    </details>
  );
}
