"use client";

import { useEffect, useState, useCallback } from "react";
import { MessageSquare } from "lucide-react";
import { api } from "@/lib/api";
import type { Document } from "@/lib/types";
import UploadPanel from "@/components/library/UploadPanel";
import FolderSection from "@/components/library/FolderSection";
import ChatPanel from "@/components/library/ChatPanel";
import type { Reminder } from "@/components/library/RemindersPanel";
import type { WorkspaceFile } from "@/components/library/DraftsPanel";

export default function LibraryPage() {
  const [docs, setDocs] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);

  // Q&A state
  const [qaOpen, setQaOpen] = useState(false);
  const [selectedDocs, setSelectedDocs] = useState<Set<string>>(new Set());

  // Reminders
  const [reminders, setReminders] = useState<Reminder[]>([]);

  // Drafts (text editor tool workspace files)
  const [draftFiles, setDraftFiles] = useState<WorkspaceFile[]>([]);

  const loadDocs = useCallback(() => {
    api.documents.list().then((data) => {
      setDocs(data);
      setLoading(false);
    });
  }, []);

  const loadReminders = useCallback(() => {
    api.reminders
      .list()
      .then((data) => setReminders(data as Reminder[]))
      .catch(() => {});
  }, []);

  const loadDrafts = useCallback(() => {
    api.workspace
      .list()
      .then((data) => setDraftFiles(data))
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
    loadDrafts();
  }, [loadDrafts]);

  const handleDeleteReminder = async (id: string) => {
    await api.reminders.delete(id).catch(() => {});
    setReminders((prev) => prev.filter((r) => r.id !== id));
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

      <UploadPanel onUploaded={loadDocs} />

      <FolderSection
        docs={docs}
        setDocs={setDocs}
        loading={loading}
        selectedDocs={selectedDocs}
        setSelectedDocs={setSelectedDocs}
        loadDocs={loadDocs}
      />

      {qaOpen && (
        <ChatPanel
          selectedDocs={selectedDocs}
          reminders={reminders}
          onDeleteReminder={handleDeleteReminder}
          loadReminders={loadReminders}
          draftFiles={draftFiles}
          loadDrafts={loadDrafts}
          onClose={() => setQaOpen(false)}
        />
      )}
    </div>
  );
}
