# API

The app's own frontend uses this API, and scripts or other tools can too. Writes from a browser must come from
the app's own origin exactly (shared sites and previews have their own origins and are refused). Scripts send no `Origin` header and authenticate with a site's **edit token**:
`Authorization: Bearer <edit token>`. The site's owner can see and replace the token in `/app`.

## Sites

| Method | Path | Auth |
| --- | --- | --- |
| `POST` | `/api/sites` (multipart: zip, name, note, optional slug, optional entry) | signed in |
| `POST` | `/api/sites/<id>/versions` (multipart: zip, note, optional entry) | owner or token |
| `POST` | `/api/sites/<id>/versions/<n>/entry` `{"entry": "home.html"}` | owner or token |
| `POST` | `/api/sites/<id>/live` `{"version": n}` | owner or token |
| `POST` | `/api/sites/<id>/token` (replace the edit token) | owner or token |
| `POST` | `/api/sites/<id>/slug` `{"slug": "learn-sql"}` | owner or token |
| `GET` | `/api/slugs/<name>` (is it free?) | none |
| `DELETE` | `/api/sites/<id>/versions/<n>` (not the live one) | owner or token |
| `DELETE` | `/api/sites/<id>` (unpublish) | owner, token or admin |
| `POST` | `/api/sites/<id>/claim` (attach a token-made site to your account) | signed in + token |
| `GET` | `/api/sites/<id>` | none |
| `GET` | `/api/sites/<id>/versions/<n>/files` → `{"entry", "files": {path: digest}}` | owner or token |
| `GET` | `/api/sites/<id>/files/<digest>` (original bytes, as a sandboxed download) | owner or token |
| `GET` | `/api/sites/<id>/hold` → `{"held", "reasons"}` | owner or token |

## Safety checks

- **Executables are refused** (400), by extension (`lib/blocked-files.json`: `.exe`, `.msi`, `.dmg`, `.apk`, `.jar`,
  `.dll`, `.so`, `.app` folders, ...) and by content (Windows, Linux and Mac programs, APK/JAR zips):
  `{"error": "Executable files can't be shared: tools/setup.exe"}`.
- **Held for review:** uploads that look like phishing, a seed-phrase grab or a crypto miner are stored, but the
  site is held: its links answer **451** until an admin approves it. Create and publish responses include
  `"held": true, "reasons": [...]` (else `"held": false`).

## Admin

Admins are the `ADMIN_EMAILS` accounts (signed in).

| Method | Path | |
| --- | --- | --- |
| `GET` | `/api/admin/holds?status=held` (`approved`, `rejected`, `all`) | held sites, newest first |
| `POST` | `/api/admin/holds/<site id>` `{"decision": "approve" \| "reject"}` | approve: links work again; reject: unpublished |

## Account

| Method | Path | Auth |
| --- | --- | --- |
| `GET` | `/api/auth/me` | none |
| `GET` | `/api/config` → `{"content_scheme", "content_domain"}` (where previews and sites live) | none |
| `GET` | `/api/me/sites` | signed in |
| `GET` | `/api/me/usage` → `{"used_bytes", "quota_bytes"}` | signed in |

## Abuse

| Method | Path | Auth |
| --- | --- | --- |
| `POST` | `/api/reports` `{"url", "reason", "details"?, "contact"?}` | none (10 an hour per IP) |
| `GET` | `/api/admin/reports?status=open` (`open`, `actioned`, `dismissed`, `all`) | admin |
| `POST` | `/api/admin/reports/<id>` `{"status": "actioned" or "dismissed"}` | admin |
| `GET` | `/api/admin/bans` | admin |
| `POST` | `/api/admin/bans` `{"email", "reason"}` → `{"ban", "sites_unpublished"}` | admin |
| `DELETE` | `/api/admin/bans/<email>` | admin |

- `url`: a share link of this app (`/s/<name or id>/...`) or, when `USERCONTENT_BASE_DOMAIN` is set,
  `https://<id or name>.<that domain>/...`. `reason`: `phishing`, `malware`, `scam`, `spam`, `illegal` or `other`.
  `details` up to 2,000 characters; `contact` an optional email.
- Admins are the `ADMIN_EMAILS`. An admin's `DELETE /api/sites/<id>` marks the site's open reports actioned.
- A banned account gets 403 on every share write (create, publish, live, start page, link name, token, claim),
  and so does an edit token for a banned owner's site. Banning unpublishes the account's sites; unbanning
  doesn't bring them back.

## Start page

`entry` is the page a version opens on: one of its `.html` files. Without it, the server picks `index.html`, else
the shallowest page (alphabetical). `/app` asks which page to use when there's no `index.html` and several pages,
and sends its choice with every share.

## Shared sites

- Share links are `https://spinsite.app/s/<name or id>/` (live) and `/s/<name>@<n>/` (pinned version n). API
  responses always return these; they don't depend on where content is hosted.
- Every `/s/…` (and old `/r/…`) URL redirects (308, query kept) to the site's own origin:
  `https://<site id>.sites.spinsite.app/` for the live version and `/@<n>/` for a pinned one, plus the same path.
- Content there is served with a sandbox CSP that keeps the site's own origin (its own `localStorage`,
  IndexedDB and cookies), `Origin-Agent-Cluster: ?1`, and `Access-Control-Allow-Origin: *` on non-HTML files.
  Service workers are refused (403). The app's API and cookies don't exist on these hosts.

## Limits

- **Per version:** 4 MB zipped, 50 MB unzipped, 2,000 files.
- **Per account:** 50 sites, 200 versions a site, 100 uploads a day, and 250 MB of storage
  (`STORAGE_QUOTA_MB`). A file used by many versions counts once.
- **Daily uploads:** edit-token uploads count too. They're charged to the site's owner, or to the site itself
  when it has no owner.
- **Hidden files:** `.git`, `.env` and other dotfiles are dropped on upload.
- **Reserved names:** a top-level folder or file named `@` plus digits (e.g. `@3/`) is refused: `/@3/` opens version 3.

Errors come back as `{"error": "..."}` with a matching status: 401/403 for auth, 404, 409 (e.g. deleting the
live version), 413 (too big or over quota), 429 (a limit reached, e.g. too many reports) and 451 on a
held site's links.
