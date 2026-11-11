# Architecture

```
Browser ─┬─ public/index.html  landing page at / (static, taste-skill kit in public/assets)
         ├─ app/app/page.tsx (the tool, at /app)
         │    ├─ lib/ingest.ts      files/folders/zip → {path: bytes}   (fflate)
         │    ├─ lib/localStore.ts  IndexedDB: sites + content-addressed files (idb)
         │    └─ lib/share.ts       zip a version → POST /api/...
         └─ public/sw.js  Service Worker, scope /preview/ → serves files from IndexedDB

Vercel ─── api/index.py (FastAPI)  ── Postgres (Neon): metadata · private Blob: files
              ├─ /api/sites ...        create / add version / set live / get meta
              ├─ /s/<id>[@n]/<path>    HTML: wrapper page (frames /r/, keeps localStorage); other files as-is
              ├─ /r/<id>[@n]/<path>    shared files (sandbox CSP, CORS *); HTML gets the storage script
              ├─ _core/frames.py       wrapper page + in-frame storage script
              ├─ _core/sites.py        zip validation, versions, access, limits, cleanup
              ├─ _core/db.py           SQLAlchemy tables; Postgres via DATABASE_URL, else SQLite (.data/)
              └─ _core/storage.py      LocalStorage (.data/) | BlobStorage (Vercel Blob REST)
```

## Landing page
- Built with the taste-skill (`~/.claude/skills/design-taste-frontend`). Kit copied to `public/assets`
  (no photos/objects/scenes). Dealt: split hero, gradient-mesh effect, pinned-chapters moment, palette
  forest-pale-lime, type Gabarito + Figtree + DM Mono, radius sharp, pill buttons.
- `next.config.ts` rewrites `/` → `/index.html`. The tool reuses the same tokens in `app/globals.css`.
- Checks: `node <skill>/scripts/gate.mjs public/index.html` (run from the project root).

## Routing
- Dev: `next.config.ts` rewrites `/api/*`, `/s/*` and `/r/*` → `127.0.0.1:8000`. There's an explicit trailing-slash rule
  because `:path*` drops it. `skipTrailingSlashRedirect` stops Next stripping `/s/<id>/`.
- Python deps: Vercel reads `pyproject.toml` `[project].dependencies` (not requirements.txt) when it exists. Keep both in sync.
- Prod: `vercel.json` rewrites `/api/*`, `/s/*` and `/r/*` → `api/index.py`. `api/_core` is underscore-prefixed so
  Vercel doesn't treat it as functions.

## Sign-in (Google)
- `api/_core/auth.py`: OAuth code flow via async httpx; signed httpOnly cookie `sr_session` (HMAC, SESSION_SECRET), 30 days.
- Routes: `/api/auth/login`, `/api/auth/callback`, `/api/auth/logout`, `/api/auth/me`, `/api/me/sites`, `/api/sites/<id>/claim`.
- With GOOGLE_CLIENT_ID/SECRET set, sharing requires sign-in; the owner publishes without a token; edit tokens still work (API/MCP).
- Browser writes must send this app's Origin (shared sites send `Origin: null` and are refused).
- Unpublish sets `sites.deleted_at` (links 404); admins (ADMIN_EMAILS) can unpublish any site.
- Limits per account: 50 sites, 200 versions a site, 100 uploads a day. Hidden files are dropped (client and server). Local dev reads keys from `.env.auth.local`.

## Data (server)
- **Database** (`_core/db.py`): `sites` (owner, token hash, live version, deleted_at), `versions`,
  `version_files` (site, version, path → sha256), `slugs` (link names, never deleted; newest = current),
  `users`, `uploads` (per user per day). `/s/<ref>` resolves a random id first, then a link name; old names 308 to the current one.
  Publishing is one transaction with the site row locked (`FOR UPDATE`); serving a file is one indexed lookup.
  Schema is created on first use (`create_all`); add migrations (Alembic) before changing columns.
- **Blob** (`_core/storage.py`): only `blobs/<sha256>` file contents. Private store (`BLOB_ACCESS`); the token is
  needed for every read. Files never change, so reads use the CDN cache.
- Neon: use the pooled connection string; asyncpg runs with prepared statements off (PgBouncer) and NullPool.
- Cleanup: Vercel Cron calls `/api/cron/cleanup` daily (CRON_SECRET) and deletes blobs no published version uses
  (older than 1 hour).

## Local data (IndexedDB `site-runner` v1)
- `sites` (keyPath id): LocalSite incl. `versions[].manifest` and optional `remote {siteId, token, url, live, pushed}`
- `files` (key = sha256): Blob

## Saved localStorage for shared sites (`_core/frames.py`)
- `/s/` wrapper (our code, strict nonce CSP, `frame-src 'self'`) frames `/r/` (sandboxed, opaque origin).
- `/r/` HTML = script that asks the wrapper for a seed, installs an in-memory `localStorage`, then
  `document.write`s the original page. Writes are posted up; the wrapper stores them in its localStorage
  under `sr-site-data:<site id>` (read-modify-write per message, so tabs merge per key). Caps: 512K chars a
  site, 2M chars for all sites together.
- Wrapper only accepts messages from its own frame with origin `null`; when framed itself it sends no data.
- Frame reports path/hash/title; the wrapper mirrors them into its address bar (only under its own `/s/<ref>/`).
- Links leaving the site get `target=_top` (`allow-top-navigation-by-user-activation`).
- Tests: `tests/test_frames.py`. Browser behaviour was checked by hand in headless Chrome (see tasks.md).

## Path resolution (same in sw.js and index.py)
`/` → entry (redirect if entry is in a subfolder) · `dir/` → `dir/index.html` · `dir` with `dir/index.html` → redirect to `dir/`.
