# Requirements

## What it does
Load HTML files, a folder, or a .zip in the browser and run the site **locally** (no upload needed).
Keep **versions** of each site. Optionally **share** a version via a public link that other people can open.

## Features
- Drop/pick files, folders, or zips; unzip client-side; strip a single wrapping folder.
- Local preview on its own origin (`p-<id>.sites.spinsite.app`): /app hands the files to a service worker there
  by postMessage, so relative links/assets work and the preview can't touch the app.
- Version history per site; preview any version.
- Share: first share creates a remote site + secret **edit token**; later versions "Publish" to it.
- Live link `/s/<id>/` follows the live version; `/s/<id>@<n>/` pins a version. "Make live" = rollback/promote.
  Both redirect to the site's own host: `<link name>.sites.spinsite.app/` (its id without a name) and `/@<n>/`.
  One host per site; renaming moves it (visitors' saved data starts fresh).
- Account API tokens for CLI/MCP clients (create, list, revoke in /app).
- `spinsite` CLI (`cli/`): deploy, sites, versions, live, rename, rm, usage. (Planned) `spinsite mcp` with the same tools.

## Key decisions
- **Local-first.** Nothing leaves the device until you click Share.
- **Accounts only for sharing.** Sign in with Google to share; running locally needs no account. Edit tokens remain for one site; account API tokens (made in /app) let a CLI/MCP act as the account.
- **Security.** The app origin never runs user HTML. Each shared site and each local preview gets its own
  origin under `sites.spinsite.app` (sandbox CSP with allow-same-origin, no server cookies), so it can't read
  the app's storage, cookies or tokens, and real localStorage/IndexedDB/cookies work per site.
- **Content-addressed files** (sha256), so unchanged files aren't duplicated across versions.
- **Limits:** 4MB zipped per share (Vercel function body limit), 50MB unzipped, 2000 files; 250MB storage per
  account (`STORAGE_QUOTA_MB`; a file shared by many versions counts once). Shared versions that aren't live can be
  deleted (their pinned link 404s; numbers are never reused).
- Stack: Next.js 16 frontend + FastAPI (Vercel Python function), Vercel Blob storage in prod.
