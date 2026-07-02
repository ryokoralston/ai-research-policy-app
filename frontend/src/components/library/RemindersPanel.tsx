"use client";

import { Bell, X } from "lucide-react";

export interface Reminder {
  id: string;
  content: string;
  due_at: string;
  created_at: string;
}

interface RemindersPanelProps {
  reminders: Reminder[];
  onDelete: (id: string) => void;
}

/**
 * G-1: extracted from the "Ask Documents" chat sidebar in app/library/page.tsx.
 * Renders nothing when there are no reminders — same as the original inline
 * `{reminders.length > 0 && (...)}` guard, just moved inside the component.
 */
export default function RemindersPanel({ reminders, onDelete }: RemindersPanelProps) {
  if (reminders.length === 0) return null;

  return (
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
            onClick={() => onDelete(r.id)}
            className="text-slate-500 hover:text-red-400 transition-colors flex-shrink-0 mt-0.5"
            title="Delete reminder"
          >
            <X size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}
