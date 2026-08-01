"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { clsx } from "clsx";
import {
  Search,
  FileText,
  BookOpen,
  Shield,
  LayoutDashboard,
  Users,
  Mail,
  Settings,
  PanelLeftClose,
  PanelLeftOpen,
  LogOut,
  FlaskConical,
  UserCog,
  History,
  Drama,
  MoreHorizontal,
} from "lucide-react";
import ThemeToggle from "./ThemeToggle";
import { api, getToken, clearToken } from "@/lib/api";
import type { ResearchSession } from "@/lib/types";
import { useCurrentUser } from "./UserContext";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/research", label: "Research", icon: Search },
  { href: "/reports", label: "Reports", icon: FileText },
  { href: "/library", label: "Library", icon: BookOpen },
  { href: "/analysis", label: "Risk Analysis", icon: Shield },
  { href: "/datalab", label: "Data Lab", icon: FlaskConical },
  { href: "/debate", label: "Debate", icon: Users },
  { href: "/digest", label: "Daily Digest", icon: Mail },
];

const ADMIN_NAV_ITEMS = [
  { href: "/activity-log", label: "Activity Log", icon: History },
  { href: "/personas", label: "Personas", icon: Drama },
  { href: "/users", label: "Users", icon: UserCog },
];

const SETTINGS_NAV_ITEM = { href: "/settings", label: "Settings", icon: Settings };

// Nav entries shown before the "More" toggle. The rest stay collapsed so the
// Recent list below gets the remaining height instead of being pushed off.
const PRIMARY_NAV_COUNT = 5;

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
  const [navExpanded, setNavExpanded] = useState(false);
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

  const navItems =
    user?.role === "admin"
      ? [...NAV_ITEMS, ...ADMIN_NAV_ITEMS, SETTINGS_NAV_ITEM]
      : [...NAV_ITEMS, SETTINGS_NAV_ITEM];

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

      {/* Navigation. Collapsed mode shows every item — the icons are compact
          enough that the "More" toggle would only add a click. */}
      <nav className={clsx("p-2 space-y-1", collapsed && "flex-1")}>
        {(collapsed || navExpanded
          ? navItems
          : navItems.slice(0, PRIMARY_NAV_COUNT)
        ).map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              title={collapsed ? label : undefined}
              className={clsx(
                "flex items-center rounded-md text-sm font-medium transition-colors",
                collapsed ? "justify-center px-2 py-2" : "gap-3 px-3 py-2",
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
        {!collapsed && navItems.length > PRIMARY_NAV_COUNT && (
          <button
            onClick={() => setNavExpanded((v) => !v)}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium
                       text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors"
          >
            <MoreHorizontal size={16} className="flex-shrink-0" />
            {navExpanded ? "Less" : "More"}
          </button>
        )}
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
          <>
            <ThemeToggle />
            {user && (
              <p className="text-xs text-slate-500 px-1 truncate" title={user.email}>
                {user.email}
              </p>
            )}
            {hasToken && (
              <button
                onClick={handleLogout}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium
                           text-slate-400 hover:text-red-400 hover:bg-slate-800 transition-colors"
              >
                <LogOut size={16} className="flex-shrink-0" />
                Sign out
              </button>
            )}
          </>
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
      {sessions.map((s) => {
        const active = s.id === activeId;
        return (
          <Link
            key={s.id}
            href={`/research?session=${s.id}`}
            title={s.query}
            className={clsx(
              "block px-2 py-1.5 rounded-md border text-xs transition-colors",
              active
                ? "border-blue-500 bg-blue-600/20 text-blue-400"
                : "border-transparent text-slate-400 hover:text-slate-100 hover:bg-slate-800"
            )}
          >
            <span className="line-clamp-2 leading-snug">{s.query}</span>
          </Link>
        );
      })}
    </>
  );
}
