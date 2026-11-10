# Site Runner

Run HTML prototypes in your browser, keep every upload as a version, and share the one you pick as a link.
Live at [spinsite.vercel.app](https://spinsite.vercel.app).

- **Local first.** Drop in HTML files, a folder or a zip. A Service Worker serves them from IndexedDB, so
  relative links, CSS and scripts work, and nothing is uploaded until you press Share.
- **Versions.** Every upload is a version. Shared sites have a live link (`/s/<id>/`) that follows the live
  version and pinned links (`/s/<id>@3/`). Rolling back moves the live pointer.
- **Sign in with Google to share.** Owners publish, roll back, unpublish and replace edit tokens. Edit tokens
  also work for API and MCP clients.
- **Sandboxed.** Shared pages run with `Content-Security-Policy: sandbox` (no origin of their own), so they
  can't read the app's cookies or tokens.

## Stack

Next.js 16 (the tool at `/app`), a static landing page at `/` (`public/index.html`, built with the
taste-skill kit in `public/assets`), FastAPI as a Vercel Python function (`api/`), Vercel Blob for storage.
See [architecture.md](architecture.md).

## Local development

```bash
python3 -m venv venv && source venv/bin/activate
pip3 install -r requirements-dev.txt
npm install
cp .env.example .env.auth.local      # optional: fill in Google keys to turn on sign-in
npm run dev:api                      # FastAPI on :8000 (files go to ./.data)
npm run dev                          # Next.js on :3000, proxies /api and /s to :8000
```

Checks (CI runs the same on every push):

```bash
black --check api tests && pytest -q
npx eslint app lib && npx tsc --noEmit && npm run build
```

## Configuration

All settings are environment variables; see [.env.example](.env.example).

| Variable | Purpose |
| --- | --- |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in. Unset: sign-in off (anonymous shares locally, no shares in production) |
| `SESSION_SECRET` | Signs the session cookie |
| `ADMIN_EMAILS` | Emails allowed to unpublish any site |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob; set automatically when a store is connected |

Google OAuth client: a Web application with redirect URIs `http://localhost:3000/api/auth/callback`
and `https://<your domain>/api/auth/callback`.

## Deploying

`vercel deploy --prod`. `vercel.json` routes `/api/*` and `/s/*` to `api/index.py`; Python dependencies
come from `pyproject.toml`.

## API

| Method | Path | Auth |
| --- | --- | --- |
| `POST` | `/api/sites` (zip, name, note) | signed in |
| `POST` | `/api/sites/<id>/versions` | owner or `Authorization: Bearer <edit token>` |
| `POST` | `/api/sites/<id>/live` `{"version": n}` | owner or token |
| `POST` | `/api/sites/<id>/token` (replace token) | owner or token |
| `DELETE` | `/api/sites/<id>` (unpublish) | owner, token or admin |
| `POST` | `/api/sites/<id>/claim` | signed in + token |
| `GET` | `/api/sites/<id>`, `/api/me/sites`, `/api/auth/me` | — / signed in / — |

Limits: 4 MB zipped and 50 MB unzipped per version, 2,000 files, 50 sites per account, 200 versions per
site, 100 uploads per account per day. Hidden files (`.git`, `.env`, ...) are dropped on upload.

## Known limitations

- Local previews run on the app's origin, so a malicious zip you preview yourself could read tokens stored in
  your browser. Shared links are sandboxed. Fix: serve previews from a separate origin.
- Site metadata is a JSON file per site; two writes to one site at the same instant can lose one.
- Unpublishing keeps the files in storage (they are content-addressed and may be shared between sites).
