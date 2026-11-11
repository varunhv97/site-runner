# Architecture

```
Browser ─┬─ public/index.html  landing page at / (static, taste-skill kit in public/assets)
         ├─ app/app/page.tsx (the tool, at /app) · app/report (report abuse) · app/admin (review)
         │    ├─ lib/ingest.ts      files/folders/zip → {path: bytes}   (fflate)
         │    ├─ lib/localStore.ts  IndexedDB: sites + content-addressed files (idb)
         │    └─ lib/share.ts       zip a version → POST /api/...
         └─ public/sw.js  Service Worker, scope /preview/ → serves files from IndexedDB

Vercel ─── api/index.py (FastAPI)  ── Postgres (Neon): metadata · private Blob: files
              ├─ /api/sites ...        create / add version / set live / get meta
              ├─ /s/<id>[@n]/<path>    HTML: wrapper page (frames /r/, keeps localStorage); other files as-is
              ├─ /r/<id>[@n]/<path>    shared files (sandbox CSP, CORS *); HTML gets the storage script
              ├─ _core/frames.py       wrapper page + in-frame storage script
              ├─ _core/abuse.py        reports, bans, report rate limit
              ├─ _core/sites.py        zip validation, versions, access, limits, cleanup
              ├─ _core/scan.py         refuses executables; flags phishing / seed phrases / miners
              ├─ _core/holds.py        `holds` table: flagged sites answer 451 until an admin decides
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
- Prod: `www.spinsite.app` 308s to `spinsite.app` (one origin for cookies, IndexedDB and sign-in). The old
  `spinsite.vercel.app` redirects `/`, `/s/`, `/r/` and sign-in; its `/app` stays so local sites remain reachable.
  `vercel.json` rewrites `/api/*`, `/s/*` and `/r/*` → `api/index.py`. `api/_core` is underscore-prefixed so
  Vercel doesn't treat it as functions.

## Sign-in (Google)
- `api/_core/auth.py`: OAuth code flow via async httpx; signed httpOnly cookie `sr_session` (HMAC, SESSION_SECRET), 30 days.
- Routes: `/api/auth/login`, `/api/auth/callback`, `/api/auth/logout`, `/api/auth/me`, `/api/me/sites`, `/api/sites/<id>/claim`.
- With GOOGLE_CLIENT_ID/SECRET set, sharing requires sign-in; the owner publishes without a token; edit tokens still work (API/MCP).
- Browser writes must send this app's Origin (shared sites send `Origin: null` and are refused).
- Unpublish sets `sites.deleted_at` (links 404); admins (ADMIN_EMAILS) can unpublish any site.
- Limits per account: 50 sites, 200 versions a site (current ones; deleting frees room), 100 uploads a day,
  storage quota (below). Hidden files are dropped (client and server). Local dev reads keys from `.env.auth.local`.

## Data (server)
- **Database** (`_core/db.py`): `sites` (owner, token hash, live version, deleted_at), `versions`,
  `version_files` (site, version, path → sha256), `slugs` (link names, never deleted; newest = current),
  `users`, `uploads` (per user per day), `blobs` (digest → size), `deleted_versions` (site, n: never reused),
  `reports`, `bans`, `rate_limits` (abuse, below). `/s/<ref>` resolves a random id first, then a link name; old names 308 to the current one.
  Publishing is one transaction with the site row locked (`FOR UPDATE`); serving a file is one indexed lookup.
  Schema is created on first use (`create_all`); add migrations (Alembic) before changing columns.
- **Blob** (`_core/storage.py`): only `blobs/<sha256>` file contents. Private store (`BLOB_ACCESS`); the token is
  needed for every read. Files never change, so reads use the CDN cache.
- Neon: use the pooled connection string; asyncpg runs with prepared statements off (PgBouncer) and NullPool.
- Storage quota: usage = sizes of the distinct digests used by the owner's non-deleted sites. Checked before files
  are written (create + add version, owner's quota even when a token holder publishes; no owner = no check) → 413.
  Sizes are recorded on upload; files stored before `blobs` existed are backfilled from one storage listing (which
  returns sizes) the first time they're needed, and by the cleanup cron. `GET /api/me/usage` → `{used_bytes, quota_bytes}`.
- `DELETE /api/sites/<id>/versions/<n>` removes a non-live version's rows (409 for the live one); next n =
  max(current, deleted) + 1.
- Cleanup: Vercel Cron calls `/api/cron/cleanup` daily (CRON_SECRET) and deletes blobs no published version uses
  (older than 1 hour), and drops their `blobs` rows.

## Abuse (`_core/abuse.py`, `/report`, `/admin`)
- `/report` (app origin) posts `POST /api/reports`: the link is parsed to a ref (`/s/<ref>` on PUBLIC_BASE_URL, or
  `<ref>.<USERCONTENT_BASE_DOMAIN>`), resolved like `/s/`, stored in `reports` (status open/actioned/dismissed).
- Rate limit: fixed one-hour window per client IP (X-Real-IP / X-Forwarded-For, set by Vercel) in `rate_limits`;
  pruned by the cleanup cron.
- `/admin` (ADMIN_EMAILS) lists reports; Unpublish = the existing admin takedown, which also actions the site's reports.
- `bans` (email, sub known at ban time): `index._can_share` runs before every share write and refuses a banned
  user, or a write to a banned owner's site. A ban unpublishes the owner's sites (`deleted_by_admin`).

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

## Upload safety
- `scan.py` runs in `Upload()`: executables → 400 (extensions in `lib/blocked-files.json`, mirrored in Python and
  compared by a test; plus PE/ELF/Mach-O/APK-JAR magic bytes). Content scan (`UPLOAD_SCAN`, default on) of HTML/JS
  (512KB a file, 4MB total): phishing needs a password field + two of brand / external credential target / known
  drop (Telegram bot, Discord webhook, form relays); "Sign in with Google" text doesn't count as a brand.
- Findings → row in `holds` (site, n, reasons, status held/approved/rejected, reviewer). `Sites.resolve()` raises
  451 while any row is held, so every serving path shows "waiting for a safety review". Admins approve or reject
  (reject = admin unpublish) via `/api/admin/holds`.
- Client: `lib/executables.ts` (same list) blocks Share; `lib/secrets.ts` warns about likely keys before Share.

## Path resolution (same in sw.js and index.py)
`/` → entry (redirect if entry is in a subfolder) · `dir/` → `dir/index.html` · `dir` with `dir/index.html` → redirect to `dir/`.
