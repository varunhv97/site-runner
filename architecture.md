# Architecture

```
spinsite.app (app origin: cookies, IndexedDB with edit tokens; never runs user HTML)
  ├─ public/index.html  landing page at /
  ├─ app/app/page.tsx   the tool at /app · app/report (report abuse) · app/admin (review)
  │    ├─ lib/ingest.ts      files/folders/zip → {path: bytes} (fflate)
  │    ├─ lib/localStore.ts  IndexedDB: sites + content-addressed files (idb)
  │    ├─ lib/preview.ts     hands a version's files to p-<id>.<UC> by postMessage
  │    └─ lib/share.ts       zip a version → POST /api/...
  └─ api/index.py (FastAPI) /api/*, and /s/ + /r/ → 308 to the site's host
<name|id>.<UC>           one shared site: / live, /@<n>/ pinned   (api/_core/content.py); its one host is its current
                         link name, else its id (hosts.site_label); the id and earlier names 308 there
p-<local id>.<UC>        one local preview: /__sr/boot + /__sr/sw.js from the server, the rest from the worker
api/_core: hosts.py (origins, Host → label) · content.py · paths.py (path rules) · preview/*.js (boot, worker)
           sites.py (zips, versions, limits) · db.py (Postgres | SQLite) · storage.py (Blob | .data/)
           abuse.py (reports, bans, rate limit) · scan.py (executables; phishing/seed/miner) · holds.py (451 until reviewed)
```
`<UC>` = `USERCONTENT_BASE_DOMAIN` (`sites.spinsite.app`; dev `usercontent.localhost:8001`).

## Landing page
- Built with the taste-skill (`~/.claude/skills/design-taste-frontend`). Kit copied to `public/assets`
  (no photos/objects/scenes). Dealt: split hero, gradient-mesh effect, pinned-chapters moment, palette
  forest-pale-lime, type Gabarito + Figtree + DM Mono, radius sharp, pill buttons.
- `next.config.ts` rewrites `/` → `/index.html`. The tool reuses the same tokens in `app/globals.css`.
- Checks: `node <skill>/scripts/gate.mjs public/index.html` (run from the project root).

## Origins and routing
- `HostRouter` (api/index.py) dispatches by the Host header only (never X-Forwarded-Host). Hosts under `<UC>` reach
  only the content app: no app routes, cookie header dropped, any Set-Cookie dropped, and every response gets
  `CSP: sandbox allow-same-origin allow-scripts …`, `Origin-Agent-Cluster: ?1`, `nosniff`. Other hosts get the
  app routes, unless `CONTENT_ONLY=1` (the content deployment: 404). Ids have no hyphen, so `p-` can't clash.
- Site hosts take ids only (never link names). `Service-Worker: script` → 403 there (takedowns stick).
  Uploads with a top-level `@<digits>` folder are refused (it would hide behind `/@n/`).
- `/s/<ref>[@n]/<path>` (and the old `/r/`) resolve the link name (old ones too) and 308 to
  `<scheme>://<id>.<UC>/[@n/]<path>?<query>`. Share links stay `spinsite.app/s/<slug>/`.
- Cookies are `__Host-sr_session` / `__Host-sr_oauth_state` over https (a subdomain can't plant them); plain
  names on http dev. `_check_origin` requires Origin == PUBLIC_BASE_URL exactly, so site subdomains are refused.
- Dev: `next.config.ts` rewrites `/api/*`, `/s/*` and `/r/*` → `127.0.0.1:8000`; `npm run dev:uc` serves `<UC>` on
  :8001. There's an explicit trailing-slash rule because `:path*` drops it.
- Python deps: Vercel reads `pyproject.toml` `[project].dependencies` (not requirements.txt) when it exists. Keep both in sync.
- Prod: `www.spinsite.app` 308s to `spinsite.app` (one origin for cookies, IndexedDB and sign-in). The old
  `spinsite.vercel.app` redirects `/`, `/s/`, `/r/` and sign-in; its `/app` stays so local sites remain reachable.
  `vercel.json` rewrites `/api/*`, `/s/*` and `/r/*` → `api/index.py`. `api/_core` is underscore-prefixed so
  Vercel doesn't treat it as functions. `<UC>` is a second Vercel project from this repo (docs/deploying.md):
  Framework Other, empty output dir, `CONTENT_ONLY=1`, and a host-conditioned catch-all rewrite to the function.

## Sign-in (Google)
- `api/_core/auth.py`: OAuth code flow via async httpx; signed httpOnly session cookie (HMAC, SESSION_SECRET), 30 days.
- Cookie names get the `__Host-` prefix over https (see Origins). Routes: `/api/auth/login`, `/api/auth/callback`, `/api/auth/logout`, `/api/auth/me`, `/api/me/sites`, `/api/sites/<id>/claim`.
- With GOOGLE_CLIENT_ID/SECRET set, sharing requires sign-in; the owner publishes without a token; edit tokens still work (API/MCP).
- Browser writes must send this app's exact Origin (shared sites and previews have their own and are refused).
- Unpublish sets `sites.deleted_at` (links 404); admins (ADMIN_EMAILS) can unpublish any site.
- Limits per account: 50 sites, 200 versions a site (current ones; deleting frees room), 100 uploads a day,
  storage quota (below). Hidden files are dropped (client and server). Local dev reads keys from `.env.auth.local`.

## Data (server)
- **Database** (`_core/db.py`): `sites` (owner, token hash, live version, deleted_at), `versions`,
  `version_files` (site, version, path → sha256), `slugs` (link names, never deleted; newest = current),
  `users`, `uploads` (per user per day), `blobs` (digest → size), `deleted_versions` (site, n: never reused),
  `reports`, `bans`, `rate_limits` (abuse, below), `holds` (upload safety, below). `/s/<ref>` resolves a random id first, then a link name (old names too), then 308s to the id host.
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

## Local preview hand-off (files never leave the browser)
1. /app frames `p-<lid>.<UC>/__sr/boot` (`sandbox="allow-scripts allow-same-origin allow-forms allow-popups
   allow-modals"`). Boot (our code, nonce CSP, `frame-ancestors` = app) registers `/__sr/sw.js` (scope /,
   `Service-Worker-Allowed: /`), waits for control, posts `{sr:"ready"}` to parent/opener at the app's exact origin.
2. /app accepts it only from that iframe's window (or the tab it opened) and exact origin, then posts
   `{sr:"files", version, entry, files}` (ArrayBuffers transferred) to that origin only. Nothing else is sent.
3. Boot accepts files only from the app origin + parent/opener, writes them and a manifest to Cache Storage,
   acks `loaded`, drops `window.opener`, `location.replace("/")`. The worker serves `/…` with the shared path
   rules and the sandbox headers; `/__sr/*` always goes to the network.
4. New tab = `window.open(boot)` with the same hand-off (top level has its own storage partition). No "ready"
   within 8 s (Safari can block workers in frames) → /app offers "Open preview in a new tab".
5. public/sw.js only unregisters itself; /app removes old registrations and `sr-site-data:*` keys.

## Upload safety
- `scan.py` runs in `Upload()`: executables → 400 (extensions in `lib/blocked-files.json`, mirrored in Python and
  compared by a test; plus PE/ELF/Mach-O/APK-JAR magic bytes). Content scan (`UPLOAD_SCAN`, default on) of HTML/JS
  (512KB a file, 4MB total): phishing needs a password field + two of brand / external credential target / known
  drop (Telegram bot, Discord webhook, form relays); "Sign in with Google" text doesn't count as a brand.
- Findings → row in `holds` (site, n, reasons, status held/approved/rejected, reviewer). `Sites.resolve()` raises
  451 while any row is held, so the content host (`content.py`) and the `/s/` redirect show "waiting for a safety review". Admins approve or reject
  (reject = admin unpublish) via `/api/admin/holds`.
- Client: `lib/executables.ts` (same list) blocks Share; `lib/secrets.ts` warns about likely keys before Share.

## Path resolution (`_core/paths.py` = `_core/preview/lib.js`, cases in `tests/path_rules.json`)
`/` → entry (307 if entry is in a subfolder) · `dir/` → `dir/index.html` · `dir` with `dir/index.html` → 308 to `dir/`.
Applied under `/` and `/@<n>/`. Pinned files `max-age=3600` except the start-page URL; live `no-cache`; ETag = digest.
