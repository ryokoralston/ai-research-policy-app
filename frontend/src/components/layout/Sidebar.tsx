"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { clsx } from "clsx";
import { PanelLeftClose, PanelLeftOpen, LogOut, History } from "lucide-react";
import AccountMenu from "./AccountMenu";
import { api, getToken, clearToken } from "@/lib/api";
import type { ResearchSession } from "@/lib/types";
import { useCurrentUser } from "./UserContext";
import { NAV_ITEMS, ADMIN_NAV_ITEMS, SETTINGS_NAV_ITEM } from "./navigation";

// Dispatched by the research page when a run finishes, so the Recent list picks
// up the new session without waiting for a navigation.
export const RESEARCH_UPDATED_EVENT = "research:updated";

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

export default function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [hasToken, setHasToken] = useState(false);
  const [recent, setRecent] = useState<ResearchSession[]>([]);
  const user = useCurrentUser();

  // Read the token after mount to avoid a hydration mismatch.
  useEffect(() => {
    setHasToken(Boolean(getToken()));
  }, [pathname]);

  const loadRecent = useCallback(async () => {
    if (!getToken()) return;
    try {
      setRecent(await api.research.list());
    } catch {
      // Non-critical: Recent is a convenience list, not a primary flow.
    }
  }, []);

  // Refresh on navigation (covers "ran a search, then moved pages") and on the
  // research page's completion event (covers staying put while a run finishes).
  useEffect(() => {
    loadRecent();
    window.addEventListener(RESEARCH_UPDATED_EVENT, loadRecent);
    return () => window.removeEventListener(RESEARCH_UPDATED_EVENT, loadRecent);
  }, [pathname, loadRecent]);

  const handleLogout = () => {
    clearToken();
    router.replace("/login");
  };

  // Expanded, the sidebar lists the feature pages only — admin tools and
  // Settings live in the account menu. Collapsed has no account menu (the rail
  // shows sign-out and expand), so those destinations stay in the icon list to
  // remain reachable.
  const collapsedNavItems = [
    ...NAV_ITEMS,
    ...(user?.role === "admin" ? ADMIN_NAV_ITEMS : []),
    SETTINGS_NAV_ITEM,
  ];

  return (
    <aside
      className={clsx(
        "flex-shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col transition-all duration-200",
        collapsed ? "w-14" : "w-56"
      )}
    >
      {/* Logo */}
      <div className="p-3 border-b border-slate-800 flex items-center justify-between min-h-[56px]">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-7 h-7 flex-shrink-0 rounded bg-blue-600 flex items-center justify-center text-white text-xs font-bold">
            AI
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <p className="text-sm font-semibold text-slate-100 leading-tight">
                AI Policy Research Assistant
              </p>
            </div>
          )}
        </div>
        {!collapsed && (
          <button
            onClick={onToggle}
            className="p-1 text-slate-500 hover:text-slate-100 rounded transition-colors flex-shrink-0"
            title="Collapse sidebar"
          >
            <PanelLeftClose size={16} />
          </button>
        )}
      </div>

      {/* Navigation */}
      <nav className={clsx("p-2 space-y-0.5", collapsed && "flex-1")}>
        {(collapsed ? collapsedNavItems : NAV_ITEMS).map(({ href, label, icon: Icon }) => {
          // No entry points at "/" any more (it redirects to /research), so a
          // prefix match is enough — it also keeps Reports lit on /reports/new.
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              title={collapsed ? label : undefined}
              className={clsx(
                "flex items-center rounded-md text-sm font-medium transition-colors",
                collapsed ? "justify-center px-2 py-1.5" : "gap-3 px-3 py-1.5",
                active
                  ? "bg-blue-600/20 text-blue-400"
                  : "text-slate-400 hover:text-slate-100 hover:bg-slate-800"
              )}
            >
              <Icon size={16} className="flex-shrink-0" />
              {!collapsed && label}
            </Link>
          );
        })}
      </nav>

      {/* Recent research sessions. Hidden when collapsed — the rail is too
          narrow for query text, and icons alone wouldn't identify a session. */}
      {!collapsed && (
        <div className="flex-1 min-h-0 flex flex-col border-t border-slate-800 pt-2">
          <p className="flex items-center gap-1.5 px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
            <History size={12} />
            Recent
          </p>
          <div className="flex-1 min-h-0 overflow-y-auto px-2 pb-2 space-y-0.5">
            {recent.length === 0 ? (
              <p className="px-2 py-1 text-xs text-slate-500">No research yet.</p>
            ) : (
              // Suspense-wrapped because RecentLinks reads the ?session= param:
              // useSearchParams needs a boundary or every statically rendered
              // page in this layout fails to build.
              <Suspense fallback={null}>
                <RecentLinks sessions={recent} />
              </Suspense>
            )}
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="p-2 border-t border-slate-800 space-y-2">
        {collapsed ? (
          <>
            {hasToken && (
              <button
                onClick={handleLogout}
                className="w-full flex justify-center p-2 text-slate-500 hover:text-red-400 rounded transition-colors"
                title="Sign out"
              >
                <LogOut size={16} />
              </button>
            )}
            <button
              onClick={onToggle}
              className="w-full flex justify-center p-2 text-slate-500 hover:text-slate-100 rounded transition-colors"
              title="Expand sidebar"
            >
              <PanelLeftOpen size={16} />
            </button>
          </>
        ) : (
          hasToken && <AccountMenu onSignOut={handleLogout} />
        )}
      </div>
    </aside>
  );
}

/**
 * Recent session links, split out so the `useSearchParams` call that marks the
 * open session sits inside its own Suspense boundary (see call site).
 */
function RecentLinks({ sessions }: { sessions: ResearchSession[] }) {
  const activeId = useSearchParams().get("session");

  return (
    <>
      {sessions.map((s) => (
        <RecentItem key={s.id} session={s} active={s.id === activeId} />
      ))}
    </>
  );
}

// Reveal speed for the hover scroll. Slow enough to read a long query, fast
// enough that a short overflow doesn't feel stuck.
const REVEAL_PX_PER_SECOND = 60;

/**
 * One Recent entry, clipped to a single line. Queries are routinely longer than
 * the sidebar is wide, so hovering scrolls the label to its end and leaving
 * returns it — the whole query stays reachable without spending two lines of
 * height on every entry.
 */
function RecentItem({ session, active }: { session: ResearchSession; active: boolean }) {
  const labelRef = useRef<HTMLSpanElement>(null);
  const frameRef = useRef<number | null>(null);

  const stopScrolling = () => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
  };

  const scrollLabelTo = (target: number) => {
    const el = labelRef.current;
    if (!el) return;
    stopScrolling();
    const from = el.scrollLeft;
    const distance = target - from;
    if (distance === 0) return;
    // Duration from distance, so long and short labels reveal at one speed
    // instead of long ones racing past.
    const duration = (Math.abs(distance) / REVEAL_PX_PER_SECOND) * 1000;
    const startedAt = performance.now();
    const step = (now: number) => {
      const progress = Math.min(1, (now - startedAt) / duration);
      el.scrollLeft = from + distance * progress;
      frameRef.current = progress < 1 ? requestAnimationFrame(step) : null;
    };
    frameRef.current = requestAnimationFrame(step);
  };

  // An entry can unmount mid-scroll when the list refreshes. Inlined rather
  // than reusing stopScrolling so the effect keeps an empty dep list.
  useEffect(
    () => () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    },
    []
  );

  return (
    <Link
      href={`/research?session=${session.id}`}
      title={session.query}
      onMouseEnter={() => {
        const el = labelRef.current;
        if (el) scrollLabelTo(el.scrollWidth - el.clientWidth);
      }}
      onMouseLeave={() => {
        // Snap rather than animate back: rewinding a long query at reveal speed
        // left the label mid-sentence for seconds after the pointer had gone.
        stopScrolling();
        if (labelRef.current) labelRef.current.scrollLeft = 0;
      }}
      className={clsx(
        "block px-2 py-1.5 rounded-md border text-xs transition-colors",
        active
          ? "border-blue-500 bg-blue-600/20 text-blue-400"
          : "border-transparent text-slate-400 hover:text-slate-100 hover:bg-slate-800"
      )}
    >
      <span ref={labelRef} className="block truncate">
        {session.query}
      </span>
    </Link>
  );
}
