# Configuration

All settings are environment variables. Copy [.env.example](../.env.example) to `.env.auth.local` for local
development (it's loaded by `npm run dev:api`), and set the same names in Vercel for deployments.

| Variable | Purpose |
| --- | --- |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in. Unset: sign-in is off (anonymous shares locally, no shares in production) |
| `SESSION_SECRET` | Signs the session cookie. Any long random string |
| `ADMIN_EMAILS` | Comma-separated emails allowed to unpublish any site, review reports at `/admin`, review held sites and ban accounts |
| `ABUSE_CONTACT` | Email or URL shown to banned accounts, e.g. `abuse@spinsite.app`. Default: the GitHub issues page |
| `REPORT_EMAIL` | Where each new abuse report is emailed, e.g. `abuse@spinsite.app`. Unset: reports only go to `/admin` |
| `RESEND_API_KEY` | [Resend](https://resend.com) API key for sending those emails. Unset: no email is sent |
| `MAIL_FROM` | Sender of report emails, on a domain verified in Resend. Default: `Site Runner <reports@spinsite.app>` |
| `DATABASE_URL` | Postgres, set by the Neon integration (pooled URL). Unset locally: SQLite in `./.data` |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob, set automatically when a store is connected. Unset locally: files in `./.data` |
| `BLOB_ACCESS` | `private` (default) or `public`, matching the store |
| `CRON_SECRET` | Guards the daily cleanup job (`/api/cron/cleanup`, scheduled in `vercel.json`) |
| `STORAGE_QUOTA_MB` | Storage per account in MB (may be fractional). Default 250 |
| `GLOBAL_STORAGE_LIMIT_MB` | Storage for all accounts together, in MB. Uploads past it are refused (507); past 80%, `REPORT_EMAIL` gets one email a day. Default 900 (under Vercel Blob's Hobby storage) |
| `UPLOAD_SCAN` | `on` (default) or `off`: the phishing/seed-phrase/miner scan that holds sites for review. Executables are always refused |
| `PUBLIC_BASE_URL` | The app's origin, e.g. `https://spinsite.app`: share links, sign-in, the origin check, and the only origin preview pages talk to. Required in production. Locally: the request's host (`http://localhost:3000` for previews) |
| `USERCONTENT_BASE_DOMAIN` | Where user content is served: `<link name, else site id>.<domain>` and `p-<local id>.<domain>`, e.g. `sites.spinsite.app`. Must not be the app's host or a parent of it. Served over https, except `*.localhost` (http, local dev). Required in production. Locally: `usercontent.localhost:8001` |
| `CONTENT_ONLY` | `1` on the deployment that only serves user content: no app routes on any host |

Cookies: over https the session and sign-in cookies are named `__Host-sr_session` and `__Host-sr_oauth_state`, so
a page on a subdomain (every shared site) can't set them. Over plain http (local dev) browsers refuse `__Host-`
cookies, so the names are `sr_session` and `sr_oauth_state` there.

The content deployment (second Vercel project) needs only `DATABASE_URL`, the Blob store (`BLOB_READ_WRITE_TOKEN`
or the connected store), `BLOB_ACCESS`, `PUBLIC_BASE_URL`, `USERCONTENT_BASE_DOMAIN` and `CONTENT_ONLY=1`. Never
give it `SESSION_SECRET`, `GOOGLE_*`, `CRON_SECRET` or `ADMIN_EMAILS`.

Generate a session secret with:

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(48))"
```

## Google sign-in

Create an OAuth client in Google Cloud (Google Auth Platform > Clients) of type **Web application**, with these
authorized redirect URIs:

- `http://localhost:3000/api/auth/callback`
- `https://<your domain>/api/auth/callback`
