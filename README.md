# Site Runner

Run HTML prototypes in your browser, keep every upload as a version, and share the one you pick as a link.
Live at [spinsite.vercel.app](https://spinsite.vercel.app).

- **Local first.** Drop in HTML files, a folder or a zip. A Service Worker serves them from IndexedDB, so
  relative links, CSS and scripts work, and nothing is uploaded until you press Share.
- **Versions.** Every upload is a version. Shared sites have a live link that follows the live version and
  pinned links (`@3`). Rolling back moves the live pointer.
- **Link names.** Pick a name like `/s/learn-sql/`, unique across all users. Names are never reused; after a
  rename the old name forwards to the new one.
- **Sign in with Google to share.** Owners publish, roll back, unpublish and replace edit tokens. Edit tokens
  also work for API and MCP clients.
- **Sandboxed.** Shared pages run with `Content-Security-Policy: sandbox` (no origin of their own), so they
  can't read the app's cookies or tokens. Their localStorage is still saved, per site, by a small wrapper page.

## Stack

Next.js 16 (the tool at `/app`), a static landing page at `/` (`public/index.html`, built with the
taste-skill kit in `public/assets`), FastAPI as a Vercel Python function (`api/`), Postgres (Neon) for metadata and a private Vercel Blob store for files.
See [architecture.md](architecture.md).

## Local development

```bash
python3 -m venv venv && source venv/bin/activate
pip3 install -r requirements-dev.txt
npm install
cp .env.example .env.auth.local      # optional: fill in Google keys to turn on sign-in
npm run dev:api                      # FastAPI on :8000 (SQLite + files in ./.data)
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
| `DATABASE_URL` | Postgres (set by the Neon integration). Unset locally: SQLite in `./.data` |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob; set automatically when a store is connected |
| `BLOB_ACCESS` | `private` (default) or `public`, matching the store |
| `CRON_SECRET` | Guards the daily cleanup job (`vercel.json` cron) |

Google OAuth client: a Web application with redirect URIs `http://localhost:3000/api/auth/callback`
and `https://<your domain>/api/auth/callback`.

## Deploying

The Vercel project is connected to this repo: every push to `main` deploys to production, and other
branches and pull requests get preview deployments. `vercel deploy --prod` still works by hand.
`vercel.json` routes `/api/*` and `/s/*` to `api/index.py`; Python dependencies come from `pyproject.toml`.

## API

| Method | Path | Auth |
| --- | --- | --- |
| `POST` | `/api/sites` (zip, name, note, optional slug) | signed in |
| `POST` | `/api/sites/<id>/versions` | owner or `Authorization: Bearer <edit token>` |
| `POST` | `/api/sites/<id>/live` `{"version": n}` | owner or token |
| `POST` | `/api/sites/<id>/token` (replace token) | owner or token |
| `POST` | `/api/sites/<id>/slug` `{"slug": "learn-sql"}` | owner or token |
| `GET` | `/api/slugs/<name>` (is it free?) | — |
| `DELETE` | `/api/sites/<id>/versions/<n>` (not the live one) | owner or token |
| `DELETE` | `/api/sites/<id>` (unpublish) | owner, token or admin |
| `POST` | `/api/sites/<id>/claim` | signed in + token |
| `GET` | `/api/sites/<id>`, `/api/me/sites`, `/api/me/usage`, `/api/auth/me` | — / signed in / signed in / — |

Limits: 4 MB zipped and 50 MB unzipped per version, 2,000 files, 50 sites per account, 200 versions per
site, 100 uploads per account per day, 250 MB of storage per account (`STORAGE_QUOTA_MB`; a file used by many
versions counts once). Hidden files (`.git`, `.env`, ...) are dropped on upload.

## Known limitations

- Local previews run on the app's origin, so a malicious zip you preview yourself could read tokens stored in
  your browser. Shared links are sandboxed. Fix: serve previews from a separate origin.
- Unpublished sites' files are deleted by the daily cleanup job once no other site uses them.

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## License

MIT, see [LICENSE](LICENSE). The design kit in `public/assets` has its own licence (`public/assets/LICENSE`).
