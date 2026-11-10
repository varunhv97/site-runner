import type { NextConfig } from "next";

const API = "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // Shared sites need /s/<id>/ (with slash) so relative asset paths resolve.
  skipTrailingSlashRedirect: true,
  // In dev, proxy to the local FastAPI server. In prod, vercel.json routes these to api/index.py.
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
    ];
  },
};

export default nextConfig;
