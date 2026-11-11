# spinsite

Publish and manage [Site Runner](https://spinsite.app) sites from the command line.

```sh
uv tool install "git+https://github.com/varunhv97/site-runner#subdirectory=cli"   # or pipx install ...
spinsite login              # paste a token from spinsite.app/app → API tokens
spinsite deploy ./dist --slug my-site     # a new site: my-site.sites.spinsite.app
spinsite deploy ./dist --site my-site --note "fix nav"   # a new live version
spinsite sites | versions my-site | usage
spinsite live my-site 3     # roll back or forward
spinsite rm my-site 2       # delete a version (or the whole site without a number)
spinsite rename my-site new-name
```

## MCP (Claude Code, Claude Desktop, Cursor)

`spinsite mcp` serves the same actions as MCP tools over stdio (deploy, list_sites, list_versions, set_live,
rename_site, delete_version, unpublish_site, usage, whoami). `deploy` reads the folder from disk.

```sh
claude mcp add --scope user spinsite -- spinsite mcp
```

Full guide: https://spinsite.app/docs

Every command takes `--json`. `SPINSITE_TOKEN` and `SPINSITE_URL` override the saved login
(`~/.config/spinsite/config.json`, owner-only). Hidden files and symlinks are never uploaded.
Limits: 4MB zipped, 50MB unzipped, 2000 files per version.
