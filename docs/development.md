# Running Site Runner locally

## Stack

- **`/app`**: the tool, Next.js 16 (`app/`, `lib/`).
- **`/`**: a static landing page (`public/index.html`), built with the taste-skill kit in `public/assets`.
- **`/api/*`, `/s/*`, `/r/*`**: FastAPI, run as a Vercel Python function (`api/`). `/s/` and `/r/` redirect to
  the content origin.
- **User content**: the same FastAPI app on another host. Shared sites at `http://<site id>.usercontent.localhost:8001/`,
  local previews at `http://p-<local id>.usercontent.localhost:8001/`. `*.localhost` resolves to 127.0.0.1 in
  Chrome, Firefox and curl, and counts as a secure context, so service workers work over http.
- **Storage:** Postgres (Neon) for metadata and a private Vercel Blob store for files. Locally both are replaced
  by SQLite and files in `./.data`.

See [architecture.md](../architecture.md) for how the pieces fit together.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate
pip3 install -r requirements-dev.txt
npm install
cp .env.example .env.auth.local      # optional: fill in Google keys to turn on sign-in
npm run dev:api                      # FastAPI on :8000 (SQLite + files in ./.data)
npm run dev:uc                       # user content on *.usercontent.localhost:8001 (same data)
npm run dev                          # Next.js on :3000, proxies /api, /s and /r to :8000
```

Open http://localhost:3000/app. Without Google keys, sign-in is off and sharing works anonymously (local only).
See [configuration.md](configuration.md) for every setting.

## Checks

CI runs the same on every push:

```bash
black --check api tests && pytest -q
npx eslint app lib && npx tsc --noEmit && npm run test:js && npm run build
```

## Known limitations

- Safari may block the preview's service worker inside the /app frame. /app then offers "Open preview in a
  new tab", which works everywhere.
- Tests on the content origin pass an explicit `Host` header (`<id>.sites.spinsite.app`, set in `tests/conftest.py`).
- Unpublished sites' files are deleted by the daily cleanup job once no other site uses them.
