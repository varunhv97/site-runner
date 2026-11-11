# Security

## Reporting a vulnerability

Please report security problems privately, not in a public issue: email **security@spinsite.app**, or open a
private advisory at https://github.com/varunhv97/site-runner/security/advisories/new.

Include what you found, how to reproduce it and what it affects. You'll get a reply within a week.
Please give us a reasonable time to fix it before you share details publicly.

## Scope

The app at spinsite.app, the content origin `*.sites.spinsite.app`, and this repository: bugs that let someone
get around its protections (for example, read another user's data or edit tokens, or escape a site's origin).

## Reporting abuse of a shared site

Shared sites are user content, not vulnerabilities. If a shared site is phishing, malware, a scam, spam or
illegal, report it at https://spinsite.app/report (or the "Report abuse" link in the app), or email the link to
**abuse@spinsite.app**. Each report reaches the admins by email right away and waits in the review queue at
`/admin`, where they can take sites down and ban accounts.

## How user content is isolated

- spinsite.app never serves user HTML. Each shared site runs on its own `<name>.sites.spinsite.app` (or `<site id>.…`) and each local
  preview on `p-<local id>.sites.spinsite.app`, so the browser's same-origin policy keeps them away from the app's
  IndexedDB (edit tokens), localStorage and API responses.
- Content responses carry a sandbox CSP, `Origin-Agent-Cluster: ?1` and `nosniff`. The server never sets or reads
  cookies on content hosts, and app routes don't exist there. Service workers are refused on shared sites, so a
  takedown can't be outlived by a cached worker.
- App cookies are `__Host-` cookies (no subdomain can set them). Every browser write to the API must send
  `Origin: https://spinsite.app` exactly; content origins are refused.
- Local previews get their files from /app by `postMessage`, checked by exact origin and window both ways.

## Known limitation (until sites.spinsite.app is on the Public Suffix List)

Until the listing is accepted, browsers treat every `*.sites.spinsite.app` site as the **same site** as each other
and as spinsite.app (different origins, same "site"). This means:

- A shared site can set cookies for `.sites.spinsite.app` that other shared sites receive ("cookie tossing").
  Don't rely on cookies in a shared site for anything sensitive.
- Requests from a shared site to spinsite.app count as same-site, so `SameSite=Lax` cookies are sent with them.
  Mitigated: writes need the app's exact Origin, responses have no CORS headers, and the session cookie is
  `__Host-` and httpOnly, so it can't be read, replaced or used to make changes.
- Storage (localStorage, IndexedDB), the DOM and the app's API responses stay separated by origin, as normal.
