"use client";

import { useEffect, useRef, useState } from "react";
import {
  BookOpen, Trash2, Folder, FolderOpen, ExternalLink, Globe, FileText,
  FileCode, File, Youtube, FolderPlus, Pencil, Check, X, MessageCircleQuestion,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Document } from "@/lib/types";
import Badge from "@/components/ui/Badge";
import LoadingSpinner from "@/components/ui/LoadingSpinner";
import DocumentAskModal from "./DocumentAskModal";

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

interface FolderSectionProps {
  /** Shared with the chat panel (parent state) — kept in the parent per the debt-map instructions. */
  docs: Document[];
  setDocs: React.Dispatch<React.SetStateAction<Document[]>>;
  loading: boolean;
  selectedDocs: Set<string>;
  setSelectedDocs: React.Dispatch<React.SetStateAction<Set<string>>>;
  /** Refresh the doc list from the server — called after assign/rename mutations. */
  loadDocs: () => void;
}

/**
 * G-1: extracted from app/library/page.tsx — document listing (grouped into
 * folders + standalone docs), select-all bar, and the "Add to Folder" modal
 * incl. inline folder rename. JSX/className are unchanged from the original.
 */
export default function FolderSection({ docs, setDocs, loading, selectedDocs, setSelectedDocs, loadDocs }: FolderSectionProps) {
  const [openFolders, setOpenFolders] = useState<Set<string>>(new Set());

  // "Ask this document" modal (single-document Q&A with API-native citations)
  const [askDoc, setAskDoc] = useState<Document | null>(null);

  // Folder modal state
  const [folderModalOpen, setFolderModalOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [selectedFolderId, setSelectedFolderId] = useState<string>("");

  // Folder rename state
  const [renamingFolderId, setRenamingFolderId] = useState<string | null>(null);
  const [renamingFolderName, setRenamingFolderName] = useState("");
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (renamingFolderId) renameInputRef.current?.focus();
  }, [renamingFolderId]);

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

  const statusVariant = (status: string) => {
    if (status === "indexed") return "green";
    if (status === "error") return "red";
    return "amber";
  };

  return (
    <>
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
                          onClick={() => setAskDoc(doc)}
                          disabled={doc.status !== "indexed"}
                          className="p-1.5 text-slate-500 hover:text-blue-400 disabled:opacity-30 disabled:hover:text-slate-500 transition-colors"
                          title="Ask this document"
                        >
                          <MessageCircleQuestion size={13} />
                        </button>
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
                onClick={() => setAskDoc(doc)}
                disabled={doc.status !== "indexed"}
                className="p-2 text-slate-500 hover:text-blue-400 disabled:opacity-30 disabled:hover:text-slate-500 transition-colors"
                title="Ask this document"
              >
                <MessageCircleQuestion size={14} />
              </button>
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

      {/* Ask this document modal */}
      {askDoc && <DocumentAskModal doc={askDoc} onClose={() => setAskDoc(null)} />}

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
    </>
  );
}
