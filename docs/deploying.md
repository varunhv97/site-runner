# Deploying

Site Runner runs on Vercel as **two projects from this repo**:

| Project | Domains | Serves |
| --- | --- | --- |
| app (`site-runner`) | `spinsite.app` | landing page, `/app`, `/api/*`, sign-in; `/s/` and `/r/` redirect to the content host |
| content (e.g. `site-runner-content`) | `*.sites.spinsite.app` | shared sites (`<name>.sites…`, or `<id>.sites…` without a name) and previews (`p-<id>.sites…`) only |

The content project holds no app secrets, so even a bug there can't touch sessions or sign-in.

- **Production:** every push to `main` deploys both projects. **Previews:** other branches and pull requests.
- Vercel installs Python dependencies from `pyproject.toml`. Keep it in sync with `requirements.txt`.

## Routing (`vercel.json`, shared by both projects)

- sends `/api/*`, `/s/*` and `/r/*` to `api/index.py`; on `*.sites.spinsite.app` hosts, sends **every** path there
- redirects `www.spinsite.app` and the old `spinsite.vercel.app` (shared links, landing page, sign-in) to `spinsite.app`
- adds security headers (`X-Frame-Options` etc.) everywhere except content hosts, so `/app` can frame previews
- schedules the daily cleanup job (it only does anything in the app project)

Rewrites run *after* static files. That's why the content project has an empty output directory: nothing in
`public/` (or the Next app) can be served on a content host. `api/index.py` also refuses app routes there.

## One-time setup of the content origin

Do these in order. Until step 5 the live app keeps working as before.

1. **DNS (Cloudflare, zone spinsite.app).** Add, all *DNS only* (grey cloud):
   - `NS  _acme-challenge.sites  ns1.vercel-dns.com`
   - `NS  _acme-challenge.sites  ns2.vercel-dns.com`
   - `CNAME  *.sites  cname.vercel-dns-0.com`

   The NS records delegate only the ACME challenge name to Vercel, which needs DNS-01 to issue the wildcard
   certificate. The rest of the zone stays at Cloudflare.
2. **Create the content project** in Vercel: Add New → Project → the same GitHub repo. Settings:
   - Framework Preset: **Other**
   - Build Command (override on): `mkdir -p uc-static/__sr && echo ok > uc-static/__sr/build.txt` (Vercel refuses an
     empty output folder; the placeholder sits under `/__sr/`, which is reserved, so it never hides a site's file)
   - Output Directory (override on): `uc-static`
   - Install Command (override on): `echo skip` (the Python function installs its own dependencies)
   - Root Directory: the repo root (default)
   - Until the code is on `main`: Settings → Git → Production Branch = `integration`
3. **Content project env vars** (Production and Preview): `PUBLIC_BASE_URL=https://spinsite.app`,
   `USERCONTENT_BASE_DOMAIN=sites.spinsite.app`, `CONTENT_ONLY=1`, and read access to storage: connect the same
   Neon database (`DATABASE_URL`) and the same private Blob store (`BLOB_READ_WRITE_TOKEN`, `BLOB_ACCESS=private`).
   **Do not** set `SESSION_SECRET`, `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, `CRON_SECRET` or `ADMIN_EMAILS`.
   Optional: Settings → Cron Jobs → disable (its cron call only gets a 404).
4. **Domain:** content project → Settings → Domains → add `*.sites.spinsite.app`. Wait for the certificate, deploy,
   then check `curl -I https://<a site's name or id>.sites.spinsite.app/` (200, `content-security-policy: sandbox …`) and
   `curl -I https://p-0123456789ab.sites.spinsite.app/__sr/boot` (200). Never add this domain to the app project.
5. **App project:** add `USERCONTENT_BASE_DOMAIN=sites.spinsite.app` (Production and Preview), then merge to `main`.
   Check `curl -I https://spinsite.app/s/learn-sql/` → 308 to `https://learn-sql.sites.spinsite.app/`.
6. Content project: set the Production Branch back to `main`.

Everyone signs in again once: the session cookie is renamed to `__Host-sr_session`.

## Services

- **Postgres (Neon):** connect it through the Vercel integration. It sets `DATABASE_URL`; use the pooled URL.
  Tables are created on first use. Add migrations (Alembic) before changing existing columns.
- **Vercel Blob:** a private store. Connecting it sets `BLOB_READ_WRITE_TOKEN`.
- **Storage:** `GLOBAL_STORAGE_LIMIT_MB` (default 900) caps all accounts together. Keep it under your Blob plan's
  storage and watch Vercel → Usage; you also get an email past 80%.
- **Email (abuse@ / security@):** Cloudflare Email Routing on `spinsite.app` forwards these addresses to a real
  inbox (receive only).
- **Report emails (Resend):** sign up at https://resend.com → Domains → add `spinsite.app` → add the DNS records it
  shows in Cloudflare (DNS only) → verify. Create an API key with sending access, then set `RESEND_API_KEY`,
  `REPORT_EMAIL=abuse@spinsite.app` and `ABUSE_CONTACT=abuse@spinsite.app` on the **app** project (not the content one).

Set the other variables from [configuration.md](configuration.md), and register your domain's callback URL with Google.
