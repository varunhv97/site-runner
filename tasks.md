# Tasks

## Done (2026-11-09)
- [x] Next.js 16 + FastAPI scaffold, venv, dev proxy, vercel.json
- [x] Ingest files/folders/zip, local IndexedDB store, Service Worker preview
- [x] Local version history + preview any version
- [x] Share API: create site, publish version, set live, pinned `@n` links, sandboxed serving
- [x] pytest suite (5 tests); headless-Chrome e2e verified share → publish → rollback → pinned link
- [x] Vercel project `site-runner` (Hobby) + public Blob store `site-runner-blob`; BlobStorage verified on real Blob
- [x] Preview deploy verified via `vercel curl`: share, CSS types, sandbox CSP, `/s/<id>` → `/s/<id>/`, publish, rollback, 403 w/o token

- [x] Landing page at / with taste-skill (static HTML), GATE: PASS on 2026-11-09; tool moved to /app and restyled with the same tokens

- [x] Google sign-in to share (code + 12 tests); needs Google OAuth client + env vars to switch on

- [x] Production hardening: hidden-file skip, unpublish, token rotation, admin takedown, limits, security headers, CI, README, .env.example

- [x] Metadata in Postgres (SQLite locally), files in a private Blob store (OIDC), daily cleanup cron; verified against real Neon + Blob and live on spinsite

- [x] In-app dialogs instead of browser prompt/confirm; custom link names (/s/<name>/), unique, never reused

- [x] GitHub repo (public) connected to Vercel: pushes to main deploy production

- [x] Saved localStorage for shared sites (2026-11-10): `/s/` wrapper + sandboxed `/r/` frame, per-site store,
  `tests/test_frames.py`; headless-Chrome e2e: progress survives closing the tab, sites isolated, framed-wrapper
  attack gets no data (with and without X-Frame-Options)

- [x] Storage quota per account (250MB, `STORAGE_QUOTA_MB`, unique files) + `GET /api/me/usage` + usage line in /app;
  delete a non-live shared version (`DELETE /api/sites/<id>/versions/<n>`, numbers never reused); `tests/test_quota.py`

- [x] Hardening (2026-11-10): sign-in `next` refuses backslashes/control chars (open redirect), token-only
  uploads count toward the daily limit (owner, else `site:<id>`), /api/health minimal in production, total cap
  on shared sites' saved data, MIT LICENSE, SECURITY.md

- [x] Start page picker (2026-11-10): asked when there's no index.html and several pages; changeable per version;
  sent with shares (`entry`) and changeable on shared versions

## Next
- [ ] **HIGH PRIORITY:** serve local previews from a separate origin. Today a malicious zip previewed locally
  runs same-origin as the app and can read the edit tokens in IndexedDB
- [ ] Delete the old public store `site-runner-blob` in the dashboard (CLI needs an interactive confirm)
- [ ] Disconnect Development from site-runner-db and site-runner-private (local dev uses SQLite + .data)
- [ ] Optional: a Neon `dev` branch for testing against real Postgres
- [ ] Add Alembic migrations before the first schema change
- [ ] Pre-share warning + secret scan (API keys in JS); optional expiry for old shares
- [ ] "Your shared sites" list in /app (server side exists: /api/me/sites)
- [ ] Report-abuse link and contact address
- [ ] MCP server (Python FastMCP): create_site, upload_version, list_versions, set_live, diff_versions
- [ ] Serve shared content from a separate domain (stronger isolation; would replace the localStorage bridge)
- [ ] Bridge gaps: IndexedDB and cookies aren't saved; popups opened by a shared site get no saved storage
- [ ] Larger uploads: client-direct Blob uploads to bypass the 4.5MB function limit
