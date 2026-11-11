# spinsite

Publish and manage [Site Runner](https://spinsite.app) sites from the command line.

```sh
pipx install ./cli          # or: uvx --from ./cli spinsite ...
spinsite login              # paste a token from spinsite.app/app → API tokens
spinsite deploy ./dist --slug my-site     # a new site: my-site.sites.spinsite.app
spinsite deploy ./dist --site my-site --note "fix nav"   # a new live version
spinsite sites | versions my-site | usage
spinsite live my-site 3     # roll back or forward
spinsite rm my-site 2       # delete a version (or the whole site without a number)
spinsite rename my-site new-name
```

Every command takes `--json`. `SPINSITE_TOKEN` and `SPINSITE_URL` override the saved login
(`~/.config/spinsite/config.json`, owner-only). Hidden files and symlinks are never uploaded.
Limits: 4MB zipped, 50MB unzipped, 2000 files per version.
