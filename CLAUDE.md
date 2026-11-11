# Site Runner — Claude instructions

@AGENTS.md

Read `requirements.md`, `architecture.md`, `tasks.md` at session start. Update `tasks.md` as work progresses.

## Dev
- Python venv: `venv/` (direnv auto-activates). Install: `pip3 install -r requirements-dev.txt`
- API: `npm run dev:api` (FastAPI on :8000). Content: `npm run dev:uc` (:8001, `*.usercontent.localhost`).
  Web: `npm run dev` (Next on :3000, proxies /api and /s to :8000)
- Tests: `pytest -q` and `node --test tests/path_rules.test.mjs`. Format: `black api tests cli`.
  Lint/types: `npx eslint . && npx tsc --noEmit`

## Rules
- Landing page (`public/index.html`) follows the taste-skill: never edit `public/assets/` (the kit); page CSS only inside visuals.
- Keep server (`api/_core/paths.py`) and preview service-worker (`api/_core/preview/lib.js`) path resolution
  identical; both run the cases in `tests/path_rules.json` (pytest + `node --test tests/path_rules.test.mjs`).
- The app origin never serves user HTML. Shared sites and previews are served only from subdomains of
  `USERCONTENT_BASE_DOMAIN`, with the sandbox CSP (including `allow-same-origin`), `Origin-Agent-Cluster`, no
  cookies. `HostRouter` in `api/index.py` is the boundary: content hosts reach only `_core/content.py`.
- Python HTTP calls: async `httpx` wrapped in try/except (see `api/_core/storage.py`).
- Env vars: documented in `.env.example`; local keys in `.env.auth.local` (gitignored). Never commit secrets.
- Python deps: keep `pyproject.toml` (what Vercel installs) and `requirements.txt` in sync.
- Next.js 16: read `node_modules/next/dist/docs/` before using Next APIs.
