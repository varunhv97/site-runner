# Deploying

Site Runner runs on Vercel. The project is connected to this repo:

- **Production:** every push to `main` deploys to [spinsite.app](https://spinsite.app).
- **Previews:** other branches and pull requests get preview deployments.
- **By hand:** `vercel deploy --prod` still works.

## Routing

`vercel.json` does four things:
- sends `/api/*`, `/s/*` and `/r/*` to `api/index.py`
- redirects `www.spinsite.app` to `spinsite.app`, so sign-in, cookies and browser storage stay on one origin
- redirects the old `spinsite.vercel.app` address for shared links (`/s/`, `/r/`), the landing page and
  sign-in. `/app` stays there for now, so people can still open local sites saved on that address
- schedules the daily cleanup job

Vercel installs Python dependencies from `pyproject.toml`. Keep it in sync with `requirements.txt`.

## Services

- **Postgres (Neon):** connect it through the Vercel integration. It sets `DATABASE_URL`; use the pooled URL.
  Tables are created on first use. Add migrations (Alembic) before changing existing columns.
- **Vercel Blob:** a private store. Connecting it sets `BLOB_READ_WRITE_TOKEN`.

Set the other variables from [configuration.md](configuration.md), including `PUBLIC_BASE_URL` for your
domain, and register your domain's callback URL with Google.
