const backendOrigin = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const isDev = process.env.NODE_ENV !== "production";

// Next.js emits inline <script>/<style> tags itself (hydration payload,
// styled-jsx), so script-src/style-src need 'unsafe-inline' here — a
// nonce-based CSP would be tighter but requires a middleware.ts rewrite,
// out of scope for this pass. 'unsafe-eval' is dev-only: next dev's Fast
// Refresh (webpack HMR) evaluates code as strings; the production build
// doesn't, so it's dropped there.
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "geolocation=(), camera=(), microphone=()" },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      `connect-src 'self' ${backendOrigin}${isDev ? " ws://localhost:*" : ""}`,
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; "),
  },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
