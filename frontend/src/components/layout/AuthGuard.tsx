"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, getToken } from "@/lib/api";

/**
 * Client-side gate: if the backend requires auth and we have no token, redirect
 * to /login before rendering the app. Expired/invalid tokens are caught later
 * by the 401 handler in api.ts (which also bounces to /login).
 */
export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (pathname === "/login") {
      setReady(true);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const { auth_required } = await api.auth.status();
        if (cancelled) return;
        if (auth_required && !getToken()) {
          router.replace("/login");
          return;
        }
      } catch {
        // Status check failed (backend down etc.) — fall through and let the
        // individual pages surface their own errors rather than hard-blocking.
      }
      if (!cancelled) setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [pathname, router]);

  if (!ready) return null;
  return <>{children}</>;
}
