# Site Runner

**Run HTML prototypes in your browser and share them as a link.** Try it at **[spinsite.app](https://spinsite.app)**.

Drop in a folder, a zip or a single HTML file and it runs straight away in the tab, with its styles, scripts,
images and links working as they would on a real server. Every upload is kept as a version, and when you're
ready you can share the one you pick.

## What you can do

- **Preview without uploading.** Your files stay in your browser until you press Share. No account needed.
- **Keep every version.** Each upload becomes the next version, with a note on what changed.
- **Share a link.** Sign in with Google and share a version. You get a live link that always shows the
  version you choose, and pinned links (`/s/my-site@3/`) that always show one version.
- **Roll back in one click.** Point the live link at any earlier version. Visitors see it on their next load.
- **Pick a link name.** `spinsite.app/s/my-site/` instead of a random id. Old names keep working after a rename.
- **Sites that remember things.** Shared sites can use `localStorage`, so a course remembers your progress
  or a game your high score, saved in each visitor's own browser.

## Examples

- [Learn SQL](https://spinsite.app/s/learn-sql/): a beginner SQL course with a real database in the tab.
- [Ring Studio](https://spinsite.app/s/ring-studio/): try on engagement rings on an animated hand.

## Is it safe to open a shared site?

Shared sites run in a locked-down sandbox: their scripts can't read your Site Runner account, cookies or edit
tokens. What a site saves stays in your browser, kept apart from every other shared site.

Shared sites are made by Site Runner's users. If one is phishing, malware, a scam or otherwise abusive,
[report it](https://spinsite.app/report) and we'll take it down. To report a security problem in Site Runner
itself, see [SECURITY.md](SECURITY.md).

## Limits

Each shared version can be up to 4 MB zipped (50 MB unzipped, 2,000 files). Each account can have 50 shared
sites and 250 MB of storage; a file used by many versions counts once. You can delete old shared versions to
make room. Local previews are limited only by your browser's storage.

## For developers

Site Runner is open source: Next.js and a FastAPI backend on Vercel, Postgres for metadata and Vercel Blob for
files.

- [Running it locally](docs/development.md)
- [Configuration](docs/configuration.md)
- [API](docs/api.md)
- [Deploying](docs/deploying.md)
- [Architecture](architecture.md)

Found a bug or have an idea? [Open an issue](https://github.com/varunhv97/site-runner/issues).

## License

MIT, see [LICENSE](LICENSE). The design kit in `public/assets` has its own licence (`public/assets/LICENSE`).

Made by [Varun](https://www.linkedin.com/in/varunhv/).
