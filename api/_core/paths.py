"""Path resolution for a site's files, shared by the server (shared sites) and the preview service worker.

The same rules live in api/_core/preview/lib.js; tests/path_rules.json holds the cases both must pass.
  ""          -> the start page (a redirect when it's in a subfolder, so its relative links work)
  "dir/"      -> "dir/index.html"
  "dir"       -> redirect to "dir/" when "dir/index.html" exists
"""

from collections.abc import Awaitable, Callable

# ("file", path) | ("redirect", target relative to the site's base, status) | ("missing",)
Resolved = tuple


async def resolve_path(path: str, entry: str, exists: Callable[[str], Awaitable[bool]]) -> Resolved:
    if path == "":
        if "/" in entry:
            return ("redirect", entry, 307)  # 307: the owner can change the start page
        path = entry
    elif path.endswith("/"):
        path += "index.html"
    if await exists(path):
        return ("file", path)
    if await exists(f"{path}/index.html"):
        return ("redirect", f"{path}/", 308)
    return ("missing",)
