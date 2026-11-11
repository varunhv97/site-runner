# Running Site Runner locally

## Stack

- **`/app`**: the tool, Next.js 16 (`app/`, `lib/`).
- **`/`**: a static landing page (`public/index.html`), built with the taste-skill kit in `public/assets`.
- **`/api/*`, `/s/*`, `/r/*`**: FastAPI, run as a Vercel Python function (`api/`).
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

- Local previews run on the app's own origin, so a malicious zip you preview yourself could read the edit
  tokens stored in your browser. Shared links are sandboxed. The fix (serving previews from a separate origin)
  is the top item in [tasks.md](../tasks.md).
- Unpublished sites' files are deleted by the daily cleanup job once no other site uses them.
