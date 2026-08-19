import { NextRequest, NextResponse } from "next/server";

const backendOrigin = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const isDev = process.env.NODE_ENV !== "production";

/**
 * CSP lives here rather than in next.config.mjs because it carries a
 * per-request nonce, which a static header table cannot express.
 *
 * script-src uses 'nonce-<value>' + 'strict-dynamic' instead of
 * 'unsafe-inline': Next tags its own inline bootstrap/hydration scripts with
 * the nonce it reads off the request header set below, and 'strict-dynamic'
 * lets those trusted scripts load the rest of the chunk graph. The one
 * hand-written inline script in the app (ThemeScript) gets the nonce passed
 * down from the root layout.
 *
 * 'unsafe-eval' stays dev-only: next dev's Fast Refresh (webpack HMR)
 * evaluates code as strings; the production build doesn't.
 *
 * style-src keeps 'unsafe-inline' on purpose — styled-jsx and Next's injected
 * <style> tags are not nonced, and dropping it breaks styling.
 */
export function middleware(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");

  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self' ${backendOrigin}${isDev ? " ws://localhost:*" : ""}`,
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; ");

  // Next reads the nonce and the CSP off the *request* headers to nonce the
  // framework's own inline scripts, so both must be set here as well as on
  // the response.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    /*
     * Everything except static assets — the nonce is only meaningful for
     * documents, and running this on every chunk/image request is waste.
     */
    {
      source: "/((?!_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
