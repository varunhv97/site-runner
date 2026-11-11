import type { NextConfig } from "next";

// The local FastAPI server (npm run dev:api). DEV_API_ORIGIN moves it, e.g. to run a second copy.
const API = process.env.DEV_API_ORIGIN ?? "http://127.0.0.1:8000";

const isDev = process.env.NODE_ENV === "development";
// Where shared sites and previews live: /app frames previews there (same default as api/_core/hosts.py).
const contentDomain = process.env.USERCONTENT_BASE_DOMAIN || (isDev ? "usercontent.localhost:8001" : "sites.spinsite.app");
const contentScheme = contentDomain.split(":")[0].endsWith("localhost") ? "http" : "https";

// The app's own pages (landing, /app, /report, /admin). No nonces: the pages stay static, and the landing page has
// inline scripts. Everything else is locked to this origin, so injected script couldn't load code from elsewhere
// or send data out (connect/img/frame/form). User content never runs here: it has its own CSP on <UC> hosts.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`, // React needs eval only in dev
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com",
  "img-src 'self' data: blob:",
  `connect-src 'self'${isDev ? " ws: wss:" : ""}`, // dev: hot reload
  `frame-src ${contentScheme}://*.${contentDomain}`,
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'self'",
  ...(isDev ? [] : ["upgrade-insecure-requests"]),
].join("; ");

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // /s/<ref>/ links keep their slash on the way to the API, which redirects them to the site's own host.
  skipTrailingSlashRedirect: true,
  async headers() {
    return [{ source: "/:path*", headers: [{ key: "Content-Security-Policy", value: csp }] }];
  },
  // In dev, proxy to the local FastAPI server. In prod, vercel.json routes these to api/index.py.
  // Shared sites and previews are not proxied: they live on *.usercontent.localhost:8001 (npm run dev:uc).
  async rewrites() {
    // The landing page is static HTML built with the taste-skill kit (public/index.html).
    const landing = [{ source: "/", destination: "/index.html" }];
    if (process.env.NODE_ENV !== "development") return landing;
    return [
      ...landing,
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      // :path* drops the trailing slash, so match slash-terminated paths explicitly first.
      { source: "/s/:path*/", destination: `${API}/s/:path*/` },
      { source: "/s/:path*", destination: `${API}/s/:path*` },
      { source: "/r/:path*/", destination: `${API}/r/:path*/` },
      { source: "/r/:path*", destination: `${API}/r/:path*` },
    ];
  },
};

export default nextConfig;
