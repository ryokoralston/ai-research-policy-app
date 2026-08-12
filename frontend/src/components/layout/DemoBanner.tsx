"use client";

import { useEffect, useState } from "react";
import { Info } from "lucide-react";
import { api } from "@/lib/api";

/**
 * Shown only where the backend reports DEMO_MODE (see docs/demo-instance.md).
 *
 * A demo instance differs from production in two ways a visitor would
 * otherwise discover by surprise: every signed-in account shares one
 * workspace and can see the others' work, and billable runs are capped per
 * account per day. Both belong in the app, not only in the note that came
 * with someone's credentials.
 */
export default function DemoBanner() {
  const [quota, setQuota] = useState<number | null>(null);

  useEffect(() => {
    api.auth
      .status()
      .then((s) => {
        if (s.demo_mode) setQuota(s.demo_run_quota ?? 0);
      })
      .catch(() => {
        // Non-critical: a failed status check just leaves the banner off.
      });
  }, []);

  if (quota === null) return null;

  return (
    <div className="flex items-start gap-2.5 px-6 py-2.5 bg-blue-950/40 border-b border-blue-900/60">
      <Info size={14} className="text-blue-400 mt-0.5 flex-shrink-0" />
      <p className="text-xs text-blue-200">
        <span className="font-medium">Shared demo.</span> All accounts see the same workspace, so
        research sessions, documents, and reports are visible to everyone signed in
        {quota > 0 && (
          <>
            {" "}
            — and each account can start {quota} billable runs per day, resetting at 00:00 UTC
          </>
        )}
        .
      </p>
    </div>
  );
}
