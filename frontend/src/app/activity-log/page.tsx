"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useCurrentUser } from "@/components/layout/UserContext";

type LogEntry = Awaited<ReturnType<typeof api.auditLog.list>>[number];

const PAGE_SIZE = 50;

export default function ActivityLogPage() {
  const router = useRouter();
  const currentUser = useCurrentUser();
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(true);

  useEffect(() => {
    if (currentUser && currentUser.role !== "admin") {
      router.replace("/");
      return;
    }
    if (currentUser?.role === "admin") {
      api.auditLog
        .list(PAGE_SIZE)
        .then((data) => {
          setEntries(data);
          setHasMore(data.length === PAGE_SIZE);
          setLoading(false);
        })
        .catch(() => setLoading(false));
    }
  }, [currentUser, router]);

  async function loadMore() {
    if (entries.length === 0) return;
    setLoadingMore(true);
    try {
      const cursor = entries[entries.length - 1].created_at;
      const more = await api.auditLog.list(PAGE_SIZE, cursor);
      setEntries((prev) => [...prev, ...more]);
      setHasMore(more.length === PAGE_SIZE);
    } finally {
      setLoadingMore(false);
    }
  }

  if (!currentUser || currentUser.role !== "admin" || loading) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400 text-sm">Loading…</div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto py-10 px-4">
      <h1 className="text-2xl font-bold text-slate-100 mb-1">Activity Log</h1>
      <p className="text-slate-400 text-sm mb-8">
        Logins and major changes (settings, document deletion, user management).
      </p>

      <div className="bg-slate-900 border border-slate-800 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-left text-slate-400 text-xs uppercase tracking-wide">
              <th className="px-4 py-3 font-medium">Time</th>
              <th className="px-4 py-3 font-medium">Actor</th>
              <th className="px-4 py-3 font-medium">Action</th>
              <th className="px-4 py-3 font-medium">Detail</th>
              <th className="px-4 py-3 font-medium">IP</th>
            </tr>
          </thead>
          <tbody>
            {entries.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-500">
                  No activity recorded yet.
                </td>
              </tr>
            )}
            {entries.map((e) => (
              <tr key={e.id} className="border-b border-slate-800 last:border-0">
                <td className="px-4 py-3 text-slate-400 whitespace-nowrap">
                  {new Date(e.created_at).toLocaleString()}
                </td>
                <td className="px-4 py-3 text-slate-200">{e.actor_email ?? "—"}</td>
                <td className="px-4 py-3 text-slate-300 font-mono text-xs">{e.action}</td>
                <td className="px-4 py-3 text-slate-400">{e.detail ?? "—"}</td>
                <td className="px-4 py-3 text-slate-500 text-xs">{e.ip_address ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {hasMore && entries.length > 0 && (
        <button
          onClick={loadMore}
          disabled={loadingMore}
          className="w-full mt-4 bg-slate-900 border border-slate-800 hover:bg-slate-800 disabled:opacity-50 text-slate-300 text-sm font-medium py-2.5 rounded-md transition-colors"
        >
          {loadingMore ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}
