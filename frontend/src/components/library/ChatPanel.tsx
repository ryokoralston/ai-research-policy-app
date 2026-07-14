"use client";

import { useEffect, useRef, useState } from "react";
import { X, Settings2, ChevronDown } from "lucide-react";
import { api, postStream } from "@/lib/api";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import StreamingText from "@/components/ui/StreamingText";
import RemindersPanel, { type Reminder } from "./RemindersPanel";
import DraftsPanel, { type WorkspaceFile } from "./DraftsPanel";
import type { Citation, ApiChatMessage, WebCitation } from "@/lib/types";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  citations?: Citation[];
  // Web-search citations gathered when Claude's answer drew on the web_search
  // tool (from the "complete" event's web_citations — see rag_service.py).
  webCitations?: WebCitation[];
  // Block-level messages this assistant turn produced (from the "complete"
  // event's turn_messages) — replayed as chat_history on the next turn so
  // prior tool_use/tool_result blocks survive instead of being flattened to text.
  apiMessages?: ApiChatMessage[];
  // Routing-workflow category from the "route" SSE event (see backend
  // services/query_router.py) — undefined if the event never arrived.
  routeCategory?: string;
}

interface ChatPanelProps {
  /** Shared with the folder/document list (parent state) — scopes the Q&A to selected docs. */
  selectedDocs: Set<string>;
  reminders: Reminder[];
  onDeleteReminder: (id: string) => void;
  /** Refresh the reminders list — called after a set_reminder tool call completes. */
  loadReminders: () => void;
  draftFiles: WorkspaceFile[];
  /** Refresh the drafts list — called after the text editor tool completes. */
  loadDrafts: () => void;
  onClose: () => void;
}

/**
 * Fallback label for a tool name this panel doesn't special-case above —
 * covers any tool from a connected MCP server (backend/services/mcp_bridge.py
 * prefixes every MCP tool name "mcp__{server}__{tool}"). Strips the prefix
 * and server segment and turns underscores into spaces, e.g.
 * "mcp__policy_library__read_document" -> "Using MCP tool: read document".
 * Anything else falls back to a generic "Running <name>…" label.
 */
function fallbackToolLabel(toolName: string): string {
  if (toolName.startsWith("mcp__")) {
    const rest = toolName.slice("mcp__".length);
    const sep = rest.indexOf("__");
    const bareName = sep === -1 ? rest : rest.slice(sep + 2);
    return `Using MCP tool: ${bareName.replace(/_/g, " ")}`;
  }
  return `Running ${toolName}…`;
}

/**
 * Label shown the instant a tool call starts (the "tool_pending" SSE event),
 * before its arguments have finished streaming in. No input values are
 * available yet, so this is a shorter, generic version of the per-tool
 * labels used once the full "tool" event arrives below.
 */
function pendingToolLabel(toolName: string): string {
  switch (toolName) {
    case "search_documents":
      return "Searching documents…";
    case "web_search":
      return "Searching the web…";
    case "get_current_datetime":
      return "Checking current date & time…";
    case "add_duration_to_datetime":
      return "Calculating date…";
    case "set_reminder":
      return "Setting reminder…";
    case "str_replace_based_edit_tool":
      return "Working on draft files…";
    default:
      return fallbackToolLabel(toolName);
  }
}

/**
 * Per-command label for the text editor tool's full "tool" event, once its
 * input (command + path) has finished streaming in.
 */
function textEditorToolLabel(input: Record<string, unknown> | undefined): string {
  const command = input?.command as string | undefined;
  const path = (input?.path as string | undefined) ?? "";
  switch (command) {
    case "view":
      return `Reading draft: ${path}…`;
    case "create":
      return `Creating draft: ${path}…`;
    case "str_replace":
    case "insert":
      return `Editing draft: ${path}…`;
    default:
      return "Working on draft files…";
  }
}

/**
 * G-1: extracted from app/library/page.tsx — the "Ask Documents" chat
 * sidebar (Q&A + system prompt configurator + tool-call indicator +
 * reminders). JSX/className are unchanged from the original.
 */
export default function ChatPanel({
  selectedDocs,
  reminders,
  onDeleteReminder,
  loadReminders,
  draftFiles,
  loadDrafts,
  onClose,
}: ChatPanelProps) {
  const [question, setQuestion] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [qaRunning, setQaRunning] = useState(false);
  const [toolStatus, setToolStatus] = useState<string | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [showSystemPrompt, setShowSystemPrompt] = useState(false);

  // Auto-scroll chat to bottom when new messages arrive
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  const handleAsk = async () => {
    if (!question.trim() || qaRunning) return;

    const currentQuestion = question;
    setQuestion(""); // clear input immediately for chat UX
    setQaRunning(true);
    setToolStatus(null);

    // Build history for the API from completed (non-streaming) messages.
    // Assistant turns that produced block-level history (apiMessages) replay
    // those blocks — including tool_use/tool_result — instead of the flattened
    // text, so Claude can still see what it searched in earlier turns. Older/error
    // turns without apiMessages fall back to plain text.
    const apiHistory: ApiChatMessage[] = [];
    for (const m of chatMessages) {
      if (m.streaming) continue;
      if (m.role === "user") {
        apiHistory.push({ role: "user", content: m.content });
      } else if (m.apiMessages && m.apiMessages.length > 0) {
        apiHistory.push(...m.apiMessages);
      } else if (m.content) {
        apiHistory.push({ role: "assistant", content: m.content });
      }
    }

    // Citations are cumulative across turns (see backend rag_service.answer_question) —
    // send the last completed assistant turn's citations so [N] numbering continues
    // instead of restarting at [1].
    const lastAssistant = [...chatMessages].reverse().find((m) => m.role === "assistant" && !m.streaming);
    const priorCitations = lastAssistant?.citations ?? null;

    // Show user message immediately, then add empty assistant placeholder
    setChatMessages((prev) => [
      ...prev,
      { role: "user", content: currentQuestion },
      { role: "assistant", content: "", streaming: true },
    ]);

    try {
      await postStream(
        api.documents.askUrl(),
        {
          question: currentQuestion,
          doc_ids: selectedDocs.size > 0 ? Array.from(selectedDocs) : null,
          top_k: 5,
          chat_history: apiHistory,
          custom_system: systemPrompt.trim() || null,
          prior_citations: priorCitations,
        },
        (event, data) => {
          const d = data as Record<string, unknown>;
          if (event === "route") {
            // Fires once near the start of the turn (see rag_service.py's
            // answer_question) — attach the category to the in-flight
            // assistant placeholder so it survives once streaming ends.
            const category = d.category as string;
            setChatMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === "assistant") {
                next[next.length - 1] = { ...last, routeCategory: category };
              }
              return next;
            });
          } else if (event === "tool_pending") {
            // Fires the instant Claude commits to a tool call, before its
            // arguments exist — show a generic per-tool indicator right away
            // rather than waiting for the full "tool" event.
            const toolName = d.name as string;
            setToolStatus(pendingToolLabel(toolName));
          } else if (event === "tool_progress") {
            // Live-updating query text as it streams in. search_documents has
            // eager_input_streaming enabled so this grows token-by-token;
            // web_search's query still arrives here too, just as one chunk
            // (see rag_service.py's tool_input_raw handling).
            if (d.name === "search_documents") {
              setToolStatus(`Searching documents: ${d.query as string}…`);
            } else if (d.name === "web_search") {
              setToolStatus(`Searching the web: ${d.query as string}…`);
            }
          } else if (event === "tool") {
            const toolName = d.name as string;
            const toolInput = d.input as Record<string, unknown> | undefined;
            let label: string;
            if (toolName === "search_documents") {
              label = `Searching documents: ${d.query as string}…`;
            } else if (toolName === "get_current_datetime") {
              label = "Checking current date & time…";
            } else if (toolName === "add_duration_to_datetime") {
              label = "Calculating date…";
            } else if (toolName === "set_reminder") {
              label = `Setting reminder: ${toolInput?.content as string ?? ""}…`;
            } else if (toolName === "str_replace_based_edit_tool") {
              label = textEditorToolLabel(toolInput);
            } else {
              label = fallbackToolLabel(toolName);
            }
            setToolStatus(label);
          } else if (event === "token") {
            setToolStatus(null); // clear search indicator once tokens arrive
            setChatMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === "assistant") {
                next[next.length - 1] = { ...last, content: last.content + (d.text as string) };
              }
              return next;
            });
          } else if (event === "complete" || event === "error") {
            setToolStatus(null);
            setChatMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last?.role === "assistant") {
                next[next.length - 1] = {
                  ...last,
                  streaming: false,
                  citations: (d.citations as Citation[] | undefined) ?? last.citations,
                  webCitations: (d.web_citations as WebCitation[] | undefined) ?? last.webCitations,
                  apiMessages: (d.turn_messages as ApiChatMessage[] | undefined) ?? last.apiMessages,
                };
              }
              return next;
            });
            setQaRunning(false);
            // Refresh reminders in case a set_reminder tool call was made
            loadReminders();
            // Refresh drafts in case the text editor tool wrote/edited a file
            loadDrafts();
          }
        }
      );
    } catch {
      setToolStatus(null);
      setChatMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === "assistant") {
          next[next.length - 1] = { ...last, content: last.content || "Error getting response.", streaming: false };
        }
        return next;
      });
      setQaRunning(false);
    }
  };

  return (
    <div className="fixed inset-y-0 right-0 w-[480px] bg-slate-900 border-l border-slate-800 flex flex-col z-50">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-slate-800">
        <h3 className="text-slate-100 font-semibold text-sm">Ask Documents</h3>
        <div className="flex items-center gap-2">
          {chatMessages.length > 0 && (
            <button
              onClick={() => setChatMessages([])}
              className="text-xs text-slate-500 hover:text-slate-300 px-2 py-1 rounded transition-colors"
              title="Clear conversation"
            >
              Clear
            </button>
          )}
          <button onClick={onClose} className="text-slate-500 hover:text-white">
            <X size={18} />
          </button>
        </div>
      </div>

      {/* System prompt configurator */}
      <div className="border-b border-slate-800">
        <button
          onClick={() => setShowSystemPrompt((v) => !v)}
          className="w-full flex items-center gap-2 px-4 py-2 text-xs text-slate-500 hover:text-slate-300 transition-colors"
        >
          <Settings2 size={12} />
          <span>System Prompt</span>
          {systemPrompt && <span className="ml-auto text-blue-400">Custom</span>}
          <ChevronDown size={12} className={`ml-auto transition-transform ${showSystemPrompt ? "rotate-180" : ""} ${systemPrompt ? "ml-0" : ""}`} />
        </button>
        {showSystemPrompt && (
          <div className="px-4 pb-3">
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder={`Default: "You are a research assistant for an AI policy institute..."\n\nOverride with your own instructions, e.g.:\n"Explain everything as if I'm a non-expert."`}
              rows={4}
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-100 placeholder-slate-600 focus:outline-none focus:border-blue-500 resize-none"
            />
            {systemPrompt && (
              <button
                onClick={() => setSystemPrompt("")}
                className="mt-1 text-xs text-slate-500 hover:text-red-400 transition-colors"
              >
                Reset to default
              </button>
            )}
          </div>
        )}
      </div>

      {/* Scope indicator */}
      {selectedDocs.size > 0 && (
        <div className="px-4 py-2 bg-blue-900/20 border-b border-slate-800">
          <p className="text-xs text-blue-400">
            Searching {selectedDocs.size} selected document{selectedDocs.size > 1 ? "s" : ""}
          </p>
        </div>
      )}

      {/* Reminders panel */}
      <RemindersPanel reminders={reminders} onDelete={onDeleteReminder} />

      {/* Drafts panel */}
      <DraftsPanel files={draftFiles} />

      {/* Chat messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {chatMessages.length === 0 ? (
          <p className="text-slate-500 text-sm">
            Ask a question about your documents. You can ask follow-up questions too.
          </p>
        ) : (
          chatMessages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                  msg.role === "user"
                    ? "bg-blue-600 text-white rounded-br-sm"
                    : "bg-slate-800 text-slate-100 rounded-bl-sm"
                }`}
              >
                {msg.role === "assistant" ? (
                  <>
                    {msg.routeCategory && (
                      <div className="inline-flex items-center gap-2 text-xs text-slate-400 bg-slate-800/60 rounded-lg px-2 py-1 mb-1.5">
                        Route: {msg.routeCategory.replace(/_/g, " ")}
                      </div>
                    )}
                    <StreamingText text={msg.content} citations={msg.citations} webCitations={msg.webCitations} />
                    {msg.streaming && (
                      <span className="inline-flex items-center gap-1 mt-1 text-slate-400">
                        <LoadingSpinner size="sm" />
                      </span>
                    )}
                  </>
                ) : (
                  <p>{msg.content}</p>
                )}
              </div>
            </div>
          ))
        )}
        {toolStatus && (
          <div className="flex justify-start">
            <div className="flex items-center gap-2 text-xs text-slate-400 bg-slate-800/60 rounded-lg px-3 py-1.5">
              <LoadingSpinner size="sm" />
              {toolStatus}
            </div>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      {/* Input */}
      <div className="p-4 border-t border-slate-800">
        <div className="flex gap-2">
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !qaRunning && handleAsk()}
            placeholder={chatMessages.length > 0 ? "Ask a follow-up question..." : "Ask a question..."}
            className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500"
            disabled={qaRunning}
            autoFocus
          />
          <button
            onClick={handleAsk}
            disabled={qaRunning || !question.trim()}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm transition-colors"
          >
            {qaRunning ? <LoadingSpinner size="sm" /> : "Ask"}
          </button>
        </div>
      </div>
    </div>
  );
}
