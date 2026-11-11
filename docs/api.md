# API

The app's own frontend uses this API, and scripts or other tools can too. Writes from a browser must come from
the app's own origin. Scripts send no `Origin` header and authenticate with a site's **edit token**:
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

## Account

| Method | Path | Auth |
| --- | --- | --- |
| `GET` | `/api/auth/me` | none |
| `GET` | `/api/me/sites` | signed in |
| `GET` | `/api/me/usage` → `{"used_bytes", "quota_bytes"}` | signed in |

## Start page

`entry` is the page a version opens on: one of its `.html` files. Without it, the server picks `index.html`, else
the shallowest page (alphabetical). `/app` asks which page to use when there's no `index.html` and several pages,
and sends its choice with every share.

## Shared sites

- `/s/<name or id>/` follows the live version; `/s/<name>@<n>/` pins version n.
- HTML pages under `/s/` are a small wrapper page that frames the sandboxed copy at `/r/...` and keeps the
  site's `localStorage`. Other files are served as they are, with `Access-Control-Allow-Origin: *`.

## Limits

- **Per version:** 4 MB zipped, 50 MB unzipped, 2,000 files.
- **Per account:** 50 sites, 200 versions a site, 100 uploads a day, and 250 MB of storage
  (`STORAGE_QUOTA_MB`). A file used by many versions counts once.
- **Daily uploads:** edit-token uploads count too. They're charged to the site's owner, or to the site itself
  when it has no owner.
- **Hidden files:** `.git`, `.env` and other dotfiles are dropped on upload.

Errors come back as `{"error": "..."}` with a matching status: 401/403 for auth, 404, 409 (e.g. deleting the
live version), 413 (too big or over quota) and 429 (a limit reached).
