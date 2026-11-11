# Configuration

All settings are environment variables. Copy [.env.example](../.env.example) to `.env.auth.local` for local
development (it's loaded by `npm run dev:api`), and set the same names in Vercel for deployments.

| Variable | Purpose |
| --- | --- |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in. Unset: sign-in is off (anonymous shares locally, no shares in production) |
| `SESSION_SECRET` | Signs the session cookie. Any long random string |
| `ADMIN_EMAILS` | Comma-separated emails allowed to unpublish any site, review reports at `/admin` and ban accounts |
| `ABUSE_CONTACT` | Email or URL shown to banned accounts. Default: the GitHub issues page |
| `DATABASE_URL` | Postgres, set by the Neon integration (pooled URL). Unset locally: SQLite in `./.data` |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob, set automatically when a store is connected. Unset locally: files in `./.data` |
| `BLOB_ACCESS` | `private` (default) or `public`, matching the store |
| `CRON_SECRET` | Guards the daily cleanup job (`/api/cron/cleanup`, scheduled in `vercel.json`) |
| `STORAGE_QUOTA_MB` | Storage per account in MB (may be fractional). Default 250 |
| `PUBLIC_BASE_URL` | The public origin used in share links and sign-in, e.g. `https://spinsite.app`. Default: the request's host |

Generate a session secret with:

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(48))"
```

## Google sign-in

Create an OAuth client in Google Cloud (Google Auth Platform > Clients) of type **Web application**, with these
authorized redirect URIs:

- `http://localhost:3000/api/auth/callback`
- `https://<your domain>/api/auth/callback`
