# Requirements

## What it does
Load HTML files, a folder, or a .zip in the browser and run the site **locally** (no upload needed).
Keep **versions** of each site. Optionally **share** a version via a public link that other people can open.

## Features
- Drop/pick files, folders, or zips; unzip client-side; strip a single wrapping folder.
- Local preview served by a Service Worker from IndexedDB, so relative links/assets work.
- Version history per site; preview any version.
- Share: first share creates a remote site + secret **edit token**; later versions "Publish" to it.
- Live link `/s/<id>/` follows the live version; `/s/<id>@<n>/` pins a version. "Make live" = rollback/promote.
- (Planned) MCP server so agents can publish/rollback with the edit token.

## Key decisions
- **Local-first.** Nothing leaves the device until you click Share.
- **Accounts only for sharing.** Sign in with Google to share; running locally needs no account. Edit tokens remain for API/MCP.
- **Security.** Shared sites are served with `Content-Security-Policy: sandbox …` (opaque origin), so they
  can't read this app's storage/cookies. Cost: shared sites can't use localStorage/cookies themselves.
- **Content-addressed files** (sha256), so unchanged files aren't duplicated across versions.
- **Limits:** 4MB zipped per share (Vercel function body limit), 50MB unzipped, 2000 files.
- Stack: Next.js 16 frontend + FastAPI (Vercel Python function), Vercel Blob storage in prod.
