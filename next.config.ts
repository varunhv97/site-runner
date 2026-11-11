import type { NextConfig } from "next";

// The local FastAPI server (npm run dev:api). DEV_API_ORIGIN moves it, e.g. to run a second copy.
const API = process.env.DEV_API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // /s/<ref>/ links keep their slash on the way to the API, which redirects them to the site's own host.
  skipTrailingSlashRedirect: true,
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
