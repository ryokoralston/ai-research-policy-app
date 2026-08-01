"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronUp, LogOut } from "lucide-react";
import ThemeToggle from "./ThemeToggle";

/**
 * Sidebar footer account row: one line that opens a popover holding the theme
 * control and sign-out. Replaces a stacked footer (theme row + email + sign-out
 * button) that cost ~118px of sidebar height — height the nav and Recent list
 * use better. Theme lives one click deep rather than buried: a user dropdown is
 * a documented placement for it, unlike a nested settings page.
 */
export default function AccountMenu({
  email,
  onSignOut,
}: {
  email?: string;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  const initial = email?.[0]?.toUpperCase() ?? "?";

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md
                   text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors"
      >
        <span
          className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-600/20 text-blue-400
                     flex items-center justify-center text-[11px] font-semibold"
        >
          {initial}
        </span>
        <span className="flex-1 min-w-0 text-xs text-left truncate" title={email}>
          {email ?? "Account"}
        </span>
        <ChevronUp size={13} className="flex-shrink-0" />
      </button>

      {/* Opens upward — the trigger is the bottom-most element in the sidebar. */}
      {open && (
        <div
          className="absolute left-0 right-0 bottom-full mb-2 bg-slate-900 border border-slate-700
                     rounded-xl shadow-xl py-2 z-30"
        >
          {email && (
            <p className="px-3 pb-2 text-xs text-slate-400 truncate" title={email}>
              {email}
            </p>
          )}
          <div className="border-t border-slate-800 px-3 py-2 flex items-center justify-between gap-2">
            <span className="text-xs text-slate-400">Theme</span>
            <ThemeToggle />
          </div>
          <div className="border-t border-slate-800 pt-1">
            <button
              onClick={() => {
                setOpen(false);
                onSignOut();
              }}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm font-medium
                         text-slate-400 hover:text-red-400 hover:bg-slate-800 transition-colors"
            >
              <LogOut size={14} className="flex-shrink-0" />
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
