"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { clsx } from "clsx";
import { ChevronUp, LogOut } from "lucide-react";
import ThemeToggle from "./ThemeToggle";
import { useCurrentUser } from "./UserContext";
import { ADMIN_NAV_ITEMS, SETTINGS_NAV_ITEM, type NavItem } from "./navigation";

/**
 * Sidebar footer account row: one line that opens a popover holding the admin
 * tools, Settings, the theme control, and sign-out. Replaces a stacked footer
 * (theme row + email + sign-out button) that cost ~118px of sidebar height —
 * height the nav and Recent list use better. Pulling the occasional
 * destinations in here is also what lets the sidebar list every feature page
 * without a More toggle.
 */
export default function AccountMenu({ onSignOut }: { onSignOut: () => void }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const pathname = usePathname();
  const user = useCurrentUser();
  const email = user?.email;

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

          {user?.role === "admin" && (
            <div className="border-t border-slate-800 pt-1">
              <p className="px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                Admin
              </p>
              {ADMIN_NAV_ITEMS.map((item) => (
                <MenuLink
                  key={item.href}
                  item={item}
                  active={pathname.startsWith(item.href)}
                  onNavigate={() => setOpen(false)}
                />
              ))}
            </div>
          )}

          <div className="border-t border-slate-800 pt-1">
            <MenuLink
              item={SETTINGS_NAV_ITEM}
              active={pathname.startsWith(SETTINGS_NAV_ITEM.href)}
              onNavigate={() => setOpen(false)}
            />
          </div>
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

function MenuLink({
  item: { href, label, icon: Icon },
  active,
  onNavigate,
}: {
  item: NavItem;
  active: boolean;
  onNavigate: () => void;
}) {
  return (
    <Link
      href={href}
      onClick={onNavigate}
      className={clsx(
        "flex items-center gap-2 px-3 py-1.5 text-sm font-medium transition-colors",
        active
          ? "text-blue-400"
          : "text-slate-400 hover:text-slate-100 hover:bg-slate-800"
      )}
    >
      <Icon size={14} className="flex-shrink-0" />
      {label}
    </Link>
  );
}
