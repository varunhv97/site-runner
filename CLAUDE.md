# Site Runner — Claude instructions

@AGENTS.md

Read `requirements.md`, `architecture.md`, `tasks.md` at session start. Update `tasks.md` as work progresses.

## Dev
- Python venv: `venv/` (direnv auto-activates). Install: `pip3 install -r requirements-dev.txt`
- API: `npm run dev:api` (FastAPI on :8000). Web: `npm run dev` (Next on :3000, proxies /api and /s to :8000)
- Tests: `pytest -q`. Format: `black api tests`. Lint/types: `npx eslint . && npx tsc --noEmit`

## Rules
- Landing page (`public/index.html`) follows the taste-skill: never edit `public/assets/` (the kit); page CSS only inside visuals.
- Schema of IndexedDB is duplicated in `lib/localStore.ts` and `public/sw.js`. Change both together.
- Keep server and service-worker path resolution identical (entry redirect, dir → index.html, slash redirect).
- Shared content must keep the `CSP: sandbox` header (no `allow-same-origin`). That's what protects edit tokens.
- Python HTTP calls: async `httpx` wrapped in try/except (see `api/_core/storage.py`).
- Env vars: documented in `.env.example`; local keys in `.env.auth.local` (gitignored). Never commit secrets.
- Python deps: keep `pyproject.toml` (what Vercel installs) and `requirements.txt` in sync.
- Next.js 16: read `node_modules/next/dist/docs/` before using Next APIs.
