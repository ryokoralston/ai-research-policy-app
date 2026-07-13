"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
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
} from "lucide-react";
import ThemeToggle from "./ThemeToggle";
import { getToken, clearToken } from "@/lib/api";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/research", label: "Research", icon: Search },
  { href: "/reports", label: "Reports", icon: FileText },
  { href: "/library", label: "Library", icon: BookOpen },
  { href: "/analysis", label: "Risk Analysis", icon: Shield },
  { href: "/datalab", label: "Data Lab", icon: FlaskConical },
  { href: "/debate", label: "Debate", icon: Users },
  { href: "/digest", label: "Daily Digest", icon: Mail },
  { href: "/settings", label: "Settings", icon: Settings },
];

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

export default function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [hasToken, setHasToken] = useState(false);

  // Read the token after mount to avoid a hydration mismatch.
  useEffect(() => {
    setHasToken(Boolean(getToken()));
  }, [pathname]);

  const handleLogout = () => {
    clearToken();
    router.replace("/login");
  };

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
              <p className="text-sm font-semibold text-slate-100 leading-tight">Policy Research</p>
              <p className="text-xs text-slate-400 leading-tight">AI Assistant</p>
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
      <nav className="flex-1 p-2 space-y-1">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
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
      </nav>

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
            <p className="text-xs text-slate-500 px-1">Powered by Claude Opus 4</p>
          </>
        )}
      </div>
    </aside>
  );
}
