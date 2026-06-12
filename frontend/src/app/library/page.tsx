"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  BookOpen, Upload, Trash2, MessageSquare, X, Folder, FolderOpen,
  ExternalLink, Globe, FileText, FileCode, File, Link, Youtube, RefreshCw,
  FolderPlus, Pencil, Check, Settings2, ChevronDown, Bell,
} from "lucide-react";
import { api, postStream } from "@/lib/api";
import type { Document } from "@/lib/types";
import Badge from "@/components/ui/Badge";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import StreamingText from "@/components/ui/StreamingText";

interface Reminder {
  id: string;
  content: string;
  due_at: string;
  created_at: string;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
}

interface CollectionMeta {
  collection_id: string;
  collection_name: string;
}

function parseMeta(json: string | null): CollectionMeta | null {
  if (!json) return null;
  try {
    const m = JSON.parse(json);
    if (m.collection_id && m.collection_name) return m as CollectionMeta;
  } catch {}
  return null;
}

interface Collection {
  id: string;
  name: string;
  docs: Document[];
}

function DocIcon({ doc }: { doc: Document }) {
  if (doc.source_type === "youtube") {
    return <Youtube size={13} className="text-red-400 flex-shrink-0" />;
  }
  if (doc.source_type === "url" || doc.source_type === "scraped") {
    return <Globe size={13} className="text-blue-400 flex-shrink-0" />;
  }
  const ext = (doc.filename || "").split(".").pop()?.toLowerCase();
  if (ext === "html" || ext === "htm") {
    return <FileCode size={13} className="text-green-400 flex-shrink-0" />;
  }
  if (ext === "txt") {
    return <FileText size={13} className="text-slate-400 flex-shrink-0" />;
  }
  return <File size={13} className="text-red-300 flex-shrink-0" />;
}

export default function LibraryPage() {
  const [docs, setDocs] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [openFolders, setOpenFolders] = useState<Set<string>>(new Set());

  // URL ingestion
  const [urlInput, setUrlInput] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState<string | null>(null);

  // Q&A state
  const [qaOpen, setQaOpen] = useState(false);
  const [selectedDocs, setSelectedDocs] = useState<Set<string>>(new Set());
  const [question, setQuestion] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [qaRunning, setQaRunning] = useState(false);
  const [toolStatus, setToolStatus] = useState<string | null>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [showSystemPrompt, setShowSystemPrompt] = useState(false);

  // Folder modal state
  const [folderModalOpen, setFolderModalOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [selectedFolderId, setSelectedFolderId] = useState<string>("");

  // Folder rename state
  const [renamingFolderId, setRenamingFolderId] = useState<string | null>(null);
  const [renamingFolderName, setRenamingFolderName] = useState("");
  const renameInputRef = useRef<HTMLInputElement>(null);

  // Reminders
  const [reminders, setReminders] = useState<Reminder[]>([]);

  const loadDocs = useCallback(() => {
    api.documents.list().then((data) => {
      setDocs(data as Document[]);
      setLoading(false);
    });
  }, []);

  const loadReminders = useCallback(() => {
    api.reminders
      .list()
      .then((data) => setReminders(data as Reminder[]))
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadDocs();
    const interval = setInterval(loadDocs, 5000);
    return () => clearInterval(interval);
  }, [loadDocs]);

  useEffect(() => {
    loadReminders();
  }, [loadReminders]);

  useEffect(() => {
    if (renamingFolderId) renameInputRef.current?.focus();
  }, [renamingFolderId]);

  // Auto-scroll chat to bottom when new messages arrive
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  const handleUpload = async (files: File[]) => {
    const invalid = files.filter(
      (f) => !["pdf", "txt", "html", "htm"].includes(f.name.split(".").pop()?.toLowerCase() ?? "")
    );
    if (invalid.length > 0) {
      alert(`Unsupported file type: ${invalid.map((f) => f.name).join(", ")}\nOnly PDF, TXT, and HTML files are supported.`);
      return;
    }
    setUploading(true);
    try {
      await Promise.all(files.map((f) => api.documents.upload(f)));
      loadDocs();
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
      loadDocs();
    } catch (err) {
      setIngestError(err instanceof Error ? err.message : "Failed to add URL");
    } finally {
      setIngesting(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this document and its index?")) return;
    await api.documents.delete(id);
    setDocs((prev) => prev.filter((d) => d.id !== id));
  };

  const handleDeleteCollection = async (collectionDocs: Document[]) => {
    if (!confirm(`Delete all ${collectionDocs.length} sources in this collection?`)) return;
    for (const doc of collectionDocs) {
      await api.documents.delete(doc.id);
    }
    setDocs((prev) => prev.filter((d) => !collectionDocs.find((cd) => cd.id === d.id)));
  };

  const handleAsk = async () => {
    if (!question.trim() || qaRunning) return;

    const currentQuestion = question;
    setQuestion(""); // clear input immediately for chat UX
    setQaRunning(true);
    setToolStatus(null);

    // Build history for the API from completed (non-streaming) messages
    // We send previous Q&A turns so Claude can reference them
    const apiHistory = chatMessages
      .filter((m) => !m.streaming)
      .map((m) => ({ role: m.role, content: m.content }));

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
        },
        (event, data) => {
          const d = data as Record<string, unknown>;
          if (event === "tool") {
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
            } else {
              label = `Running ${toolName}…`;
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
                next[next.length - 1] = { ...last, streaming: false };
              }
              return next;
            });
            setQaRunning(false);
            // Refresh reminders in case a set_reminder tool call was made
            loadReminders();
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

  const toggleDocSelection = (id: string) => {
    setSelectedDocs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) { next.delete(id); } else { next.add(id); }
      return next;
    });
  };

  const toggleFolder = (id: string) => {
    setOpenFolders((prev) => {
      const next = new Set(prev);
      if (next.has(id)) { next.delete(id); } else { next.add(id); }
      return next;
    });
  };

  const toggleCollectionSelection = (collectionDocs: Document[]) => {
    const ids = collectionDocs.map((d) => d.id);
    const allSelected = ids.every((id) => selectedDocs.has(id));
    setSelectedDocs((prev) => {
      const next = new Set(prev);
      if (allSelected) {
        ids.forEach((id) => next.delete(id));
      } else {
        ids.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  // Separate documents into collections and standalone docs
  const collections: Collection[] = [];
  const standaloneDocs: Document[] = [];
  const collectionMap = new Map<string, Collection>();

  for (const doc of docs) {
    const meta = parseMeta(doc.metadata_json);
    if (meta) {
      if (!collectionMap.has(meta.collection_id)) {
        const col: Collection = { id: meta.collection_id, name: meta.collection_name, docs: [] };
        collectionMap.set(meta.collection_id, col);
        collections.push(col);
      }
      collectionMap.get(meta.collection_id)!.docs.push(doc);
    } else {
      standaloneDocs.push(doc);
    }
  }

  const allDocIds = docs.map((d) => d.id);
  const allSelected = allDocIds.length > 0 && allDocIds.every((id) => selectedDocs.has(id));
  const someSelected = !allSelected && allDocIds.some((id) => selectedDocs.has(id));

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedDocs(new Set());
    } else {
      setSelectedDocs(new Set(allDocIds));
    }
  };

  const handleAssignFolder = async () => {
    const name = folderName.trim() ||
      (selectedFolderId ? collections.find((c) => c.id === selectedFolderId)?.name ?? "" : "");
    if (!name) return;
    const folderId = selectedFolderId || crypto.randomUUID();
    await api.documents.assignFolder(Array.from(selectedDocs), folderId, name);
    setFolderModalOpen(false);
    setFolderName("");
    setSelectedFolderId("");
    loadDocs();
  };

  const startRename = (col: Collection, e: React.MouseEvent) => {
    e.stopPropagation();
    setRenamingFolderId(col.id);
    setRenamingFolderName(col.name);
  };

  const commitRename = async () => {
    if (!renamingFolderId || !renamingFolderName.trim()) {
      setRenamingFolderId(null);
      return;
    }
    await api.documents.renameFolder(renamingFolderId, renamingFolderName.trim());
    setRenamingFolderId(null);
    loadDocs();
  };

  const handleDeleteReminder = async (id: string) => {
    await api.reminders.delete(id).catch(() => {});
    setReminders((prev) => prev.filter((r) => r.id !== id));
  };

  const statusVariant = (status: string) => {
    if (status === "indexed") return "green";
    if (status === "error") return "red";
    return "amber";
  };

  return (
    <div className="p-8 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-slate-100 mb-1">Document Library</h1>
          <p className="text-slate-400 text-sm">
            Upload files or add URLs to index and query with AI
          </p>
        </div>
        <button
          onClick={() => setQaOpen(true)}
          className="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 text-slate-100 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        >
          <MessageSquare size={15} />
          Ask Documents
        </button>
      </div>

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
              accept=".pdf,.txt,.html,.htm"
              multiple
              style={{ display: "none" }}
              onChange={(e) =>
                e.target.files && e.target.files.length > 0 && handleUpload(Array.from(e.target.files))
              }
            />
          </label>
        </p>
        <p className="text-slate-600 text-xs">PDF, TXT, HTML — multiple files supported</p>
        {uploading && <div className="mt-2"><LoadingSpinner size="sm" /></div>}
      </div>

      {loading ? (
        <div className="flex justify-center py-8"><LoadingSpinner /></div>
      ) : docs.length === 0 ? (
        <div className="text-center py-12 text-slate-500">
          <BookOpen size={36} className="mx-auto mb-3 opacity-30" />
          <p>No documents yet. Add a URL or upload a file.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {/* Select All bar */}
          <div className="flex items-center gap-3 px-4 py-2.5 bg-slate-900 border border-slate-800 rounded-xl">
            <input
              type="checkbox"
              checked={allSelected}
              ref={(el) => { if (el) el.indeterminate = someSelected; }}
              onChange={toggleSelectAll}
              className="rounded"
            />
            <span
              className="text-slate-300 text-sm cursor-pointer select-none"
              onClick={toggleSelectAll}
            >
              Select All
            </span>
            <div className="ml-auto flex items-center gap-3">
              {selectedDocs.size > 0 && (
                <>
                  <span className="text-blue-400 text-xs">
                    {selectedDocs.size} / {docs.length} selected
                  </span>
                  <button
                    onClick={() => setFolderModalOpen(true)}
                    className="flex items-center gap-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 text-xs px-3 py-1.5 rounded-lg transition-colors"
                  >
                    <FolderPlus size={13} />
                    Add to Folder
                  </button>
                </>
              )}
            </div>
          </div>

          {/* Collections */}
          {collections.map((col) => {
            const isOpen = openFolders.has(col.id);
            const colAllSelected = col.docs.every((d) => selectedDocs.has(d.id));
            const isRenaming = renamingFolderId === col.id;
            return (
              <div key={col.id} className="border border-slate-800 rounded-xl overflow-hidden">
                <div
                  className="flex items-center gap-3 p-4 bg-slate-900 hover:bg-slate-800/60 cursor-pointer transition-colors"
                  onClick={() => !isRenaming && toggleFolder(col.id)}
                >
                  <input
                    type="checkbox"
                    checked={colAllSelected}
                    onChange={() => toggleCollectionSelection(col.docs)}
                    onClick={(e) => e.stopPropagation()}
                    className="rounded"
                  />
                  {isOpen ? (
                    <FolderOpen size={16} className="text-amber-400 flex-shrink-0" />
                  ) : (
                    <Folder size={16} className="text-amber-400 flex-shrink-0" />
                  )}
                  <div className="flex-1 min-w-0" onClick={(e) => e.stopPropagation()}>
                    {isRenaming ? (
                      <div className="flex items-center gap-2">
                        <input
                          ref={renameInputRef}
                          value={renamingFolderName}
                          onChange={(e) => setRenamingFolderName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") commitRename();
                            if (e.key === "Escape") setRenamingFolderId(null);
                          }}
                          onBlur={commitRename}
                          className="flex-1 bg-slate-800 border border-blue-500 rounded px-2 py-0.5 text-sm text-slate-100 focus:outline-none"
                        />
                        <button
                          onMouseDown={(e) => { e.preventDefault(); commitRename(); }}
                          className="p-1 text-blue-400 hover:text-blue-300"
                        >
                          <Check size={14} />
                        </button>
                      </div>
                    ) : (
                      <p className="text-slate-100 text-sm font-medium truncate">{col.name}</p>
                    )}
                    <p className="text-slate-500 text-xs">{col.docs.length} documents</p>
                  </div>
                  <button
                    onClick={(e) => startRename(col, e)}
                    className="p-1.5 text-slate-500 hover:text-blue-400 transition-colors"
                    title="Rename folder"
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDeleteCollection(col.docs); }}
                    className="p-1.5 text-slate-500 hover:text-red-400 transition-colors"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>

                {isOpen && (
                  <div className="divide-y divide-slate-800 border-t border-slate-800">
                    {col.docs.map((doc) => (
                      <div
                        key={doc.id}
                        className={`flex items-center gap-3 px-4 py-3 bg-slate-950 transition-colors ${
                          selectedDocs.has(doc.id) ? "bg-blue-900/10" : ""
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={selectedDocs.has(doc.id)}
                          onChange={() => toggleDocSelection(doc.id)}
                          className="rounded ml-6"
                        />
                        <DocIcon doc={doc} />
                        <div className="flex-1 min-w-0">
                          <p className="text-slate-200 text-sm truncate">{doc.title || doc.filename}</p>
                          {doc.url && (
                            <a
                              href={doc.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-slate-500 text-xs hover:text-slate-300 flex items-center gap-1 mt-0.5 truncate"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <ExternalLink size={10} />
                              {doc.url}
                            </a>
                          )}
                        </div>
                        <Badge variant={statusVariant(doc.status) as "green" | "red" | "amber"}>
                          {doc.status}
                        </Badge>
                        <button
                          onClick={() => handleDelete(doc.id)}
                          className="p-1.5 text-slate-500 hover:text-red-400 transition-colors"
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}

          {/* Standalone docs */}
          {standaloneDocs.map((doc) => (
            <div
              key={doc.id}
              className={`bg-slate-900 border rounded-xl p-4 flex items-center gap-4 transition-colors ${
                selectedDocs.has(doc.id) ? "border-blue-500/50" : "border-slate-800"
              }`}
            >
              <input
                type="checkbox"
                checked={selectedDocs.has(doc.id)}
                onChange={() => toggleDocSelection(doc.id)}
                className="rounded"
              />
              <DocIcon doc={doc} />
              <div className="flex-1 min-w-0">
                <p className="text-slate-100 font-medium text-sm truncate">
                  {doc.title || doc.filename}
                </p>
                {doc.url && (
                  <a
                    href={doc.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-slate-500 text-xs hover:text-slate-300 flex items-center gap-1 mt-0.5 truncate"
                  >
                    <ExternalLink size={10} />
                    {doc.url}
                  </a>
                )}
                <div className="flex items-center gap-3 mt-1">
                  <Badge variant={statusVariant(doc.status) as "green" | "red" | "amber"}>
                    {doc.status}
                  </Badge>
                  {doc.page_count && (
                    <span className="text-slate-500 text-xs">{doc.page_count} pages</span>
                  )}
                  {doc.chunk_count > 0 && (
                    <span className="text-slate-500 text-xs">{doc.chunk_count} chunks</span>
                  )}
                  <span className="text-slate-600 text-xs">
                    {new Date(doc.created_at).toLocaleDateString()}
                  </span>
                </div>
              </div>
              <button
                onClick={() => handleDelete(doc.id)}
                className="p-2 text-slate-500 hover:text-red-400 transition-colors"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Folder Modal */}
      {folderModalOpen && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl p-6 w-full max-w-sm shadow-2xl">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-slate-100 font-semibold">Add to Folder</h3>
              <button
                onClick={() => { setFolderModalOpen(false); setFolderName(""); setSelectedFolderId(""); }}
                className="text-slate-500 hover:text-white"
              >
                <X size={18} />
              </button>
            </div>
            <p className="text-slate-400 text-xs mb-4">
              {selectedDocs.size} document{selectedDocs.size > 1 ? "s" : ""} selected
            </p>

            {collections.length > 0 && (
              <div className="mb-4">
                <p className="text-slate-400 text-xs mb-2">Add to existing folder:</p>
                <div className="space-y-1 max-h-36 overflow-y-auto">
                  {collections.map((col) => (
                    <button
                      key={col.id}
                      onClick={() => { setSelectedFolderId(col.id); setFolderName(""); }}
                      className={`w-full text-left flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${
                        selectedFolderId === col.id
                          ? "bg-blue-600/30 border border-blue-500/50 text-blue-300"
                          : "bg-slate-800 hover:bg-slate-700 text-slate-300"
                      }`}
                    >
                      <Folder size={13} className="text-amber-400 flex-shrink-0" />
                      {col.name}
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-2 my-3">
                  <div className="flex-1 h-px bg-slate-700" />
                  <span className="text-slate-500 text-xs">or create new</span>
                  <div className="flex-1 h-px bg-slate-700" />
                </div>
              </div>
            )}

            <input
              type="text"
              value={folderName}
              onChange={(e) => { setFolderName(e.target.value); setSelectedFolderId(""); }}
              onKeyDown={(e) => e.key === "Enter" && handleAssignFolder()}
              placeholder="New folder name..."
              className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-blue-500 mb-4"
              autoFocus
            />

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => { setFolderModalOpen(false); setFolderName(""); setSelectedFolderId(""); }}
                className="px-4 py-2 text-sm text-slate-400 hover:text-white transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleAssignFolder}
                disabled={!folderName.trim() && !selectedFolderId}
                className="flex items-center gap-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
              >
                <FolderPlus size={13} />
                Add to Folder
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Q&A Chat Panel */}
      {qaOpen && (
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
              <button onClick={() => setQaOpen(false)} className="text-slate-500 hover:text-white">
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
          {reminders.length > 0 && (
            <div className="border-b border-slate-800 px-4 py-3 space-y-1.5">
              <div className="flex items-center gap-1.5 mb-2">
                <Bell size={12} className="text-amber-400" />
                <span className="text-xs font-medium text-slate-400">Reminders</span>
              </div>
              {reminders.map((r) => (
                <div
                  key={r.id}
                  className="flex items-start gap-2 bg-slate-800/60 rounded-lg px-3 py-2"
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-slate-200 truncate">{r.content}</p>
                    <p className="text-xs text-slate-500 mt-0.5">
                      {new Date(r.due_at).toLocaleDateString("en-US", {
                        weekday: "short",
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                      })}
                    </p>
                  </div>
                  <button
                    onClick={() => handleDeleteReminder(r.id)}
                    className="text-slate-500 hover:text-red-400 transition-colors flex-shrink-0 mt-0.5"
                    title="Delete reminder"
                  >
                    <X size={13} />
                  </button>
                </div>
              ))}
            </div>
          )}

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
                        <StreamingText text={msg.content} />
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
      )}
    </div>
  );
}
