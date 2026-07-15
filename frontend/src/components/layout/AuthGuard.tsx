"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, getToken } from "@/lib/api";
import { useSetCurrentUser } from "./UserContext";

/**
 * Client-side gate: redirects to /login before rendering the app when the
 * backend still needs its first admin account (setup_required) or we have
 * no token. Expired/invalid tokens are caught later by the 401 handler in
 * api.ts (which also bounces to /login). On a valid session, fetches the
 * current user's profile into UserContext so role-gated UI (Sidebar, the
 * admin pages) can read it without a second round trip per page.
 */
export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const setUser = useSetCurrentUser();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (pathname === "/login") {
      setReady(true);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const { setup_required } = await api.auth.status();
        if (cancelled) return;
        if (setup_required || !getToken()) {
          router.replace("/login");
          return;
        }
        const me = await api.auth.me();
        if (cancelled) return;
        setUser(me);
      } catch {
        // Status/me check failed (backend down, expired token, etc.) — fall
        // through and let individual pages / the 401 handler deal with it
        // rather than hard-blocking the whole app on a transient error.
      }
      if (!cancelled) setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [pathname, router, setUser]);

  if (!ready) return null;
  return <>{children}</>;
}
