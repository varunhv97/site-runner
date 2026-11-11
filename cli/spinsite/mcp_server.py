"""`spinsite mcp`: the CLI's commands as MCP tools, over stdio, for Claude and other MCP clients.

Runs on the user's machine, so `deploy` reads a folder from disk: site files never pass through the conversation.
Uses the same login as the CLI (`spinsite login`, or SPINSITE_TOKEN / SPINSITE_URL).
"""

import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from . import __version__, config
from .bundle import BundleError, bundle
from .client import ApiError, Client

INSTRUCTIONS = """Publish and manage the user's Site Runner sites (static HTML sites shared as public links).
A site is named by its id, link name (slug) or display name. `deploy` uploads a folder from this machine: a new
site, or with `site` a new version that goes live at once. Anything deployed is public to anyone with the link.
Ask the user before `unpublish_site` or `delete_version`; to undo a bad deploy, prefer `set_live` on the previous
version. Limits: 4MB zipped, 50MB unzipped, 2000 files per version; hidden files and symlinks are skipped."""

server = MCPServer("spinsite", instructions=INSTRUCTIONS, version=__version__)

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
PUBLISH = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
CHANGE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)


def make_client() -> Client:
    return Client(config.base_url(), config.token())


async def _call(fn: Callable[[Client], Awaitable[Any]]) -> Any:
    """Run fn with a client; API and bundling problems become tool errors the model can read and act on."""
    if not config.token():
        raise ToolError(
            f"not logged in: run `spinsite login` (token from {config.base_url()}/app → API tokens) "
            "or set SPINSITE_TOKEN for this MCP server"
        )
    try:
        async with make_client() as c:
            return await fn(c)
    except (ApiError, BundleError) as e:
        raise ToolError(str(e)) from e


def _site_summary(s: dict) -> dict:
    return {
        "id": s["id"],
        "slug": s.get("slug"),
        "name": s["name"],
        "live": s["live"],
        "versions": len(s.get("versions", [])),
        "url": s.get("url"),
    }


def _published(body: dict) -> dict:
    s = body["site"]
    result = {
        "site_id": s["id"],
        "slug": s.get("slug"),
        "name": s["name"],
        "version": s["live"],
        "url": body["url"],
        "version_url": body.get("version_url"),
        "held": body.get("held", False),
    }
    if body.get("held"):
        result["held_note"] = "Held for a safety review: links show a notice until an admin approves it."
        result["reasons"] = body.get("reasons", [])
    return result


@server.tool(annotations=READ)
async def whoami() -> dict[str, Any]:
    """The account this server acts as, and the Site Runner server it talks to."""
    user = await _call(lambda c: c.me())
    return {"email": (user or {}).get("email"), "server": config.base_url()}


@server.tool(annotations=READ)
async def list_sites() -> list[dict[str, Any]]:
    """The user's shared sites: id, link name, name, live version, number of versions and link."""
    return [_site_summary(s) for s in await _call(lambda c: c.sites())]


@server.tool(annotations=READ)
async def list_versions(site: str) -> dict[str, Any]:
    """A site's versions (number, time, note, start page, size, file count) and which one is live.

    site: the site's id, link name or display name.
    """
    s = await _call(lambda c: c.find_site(site))
    return {
        "site_id": s["id"],
        "slug": s.get("slug"),
        "live": s["live"],
        "url": s.get("url"),
        "versions": s["versions"],
    }


@server.tool(annotations=READ)
async def usage() -> dict[str, Any]:
    """Storage the user's shared sites use (unique files, in bytes) and their quota."""
    return await _call(lambda c: c.usage())


@server.tool(annotations=PUBLISH)
async def deploy(
    path: str, site: str = "", name: str = "", slug: str = "", note: str = "", entry: str = ""
) -> dict[str, Any]:
    """Publish a folder (or .zip) from this machine as a public site.

    Without `site` it creates a new site; with `site` it adds a version to that site and makes it live.
    path: absolute path to the folder with the built site (relative paths resolve against the server's directory).
    site: an existing site's id, link name or name, to publish a new version of it.
    name: display name for a new site (default: the folder's name).
    slug: link name for a new site (lower-case letters, digits, hyphens), giving <slug>.sites.spinsite.app.
    note: a short note describing this version.
    entry: the page to open on when it isn't index.html, e.g. "home.html".
    """
    target = Path(path).expanduser().resolve()
    try:
        zipped, count = bundle(target)
    except BundleError as e:
        raise ToolError(str(e)) from e

    async def go(c: Client):
        if site:
            existing = await c.find_site(site)
            return await c.add_version(existing["id"], zipped, note=note, entry=entry)
        default = name or (target.stem if target.is_file() else target.name) or "site"
        return await c.create_site(zipped, name=default[:100], slug=slug.strip().lower(), note=note, entry=entry)

    return _published(await _call(go)) | {"files": count}


@server.tool(annotations=CHANGE)
async def set_live(site: str, version: int) -> dict[str, Any]:
    """Make a version live (roll back or forward): the site's link shows it from now on."""

    async def go(c: Client):
        return await c.set_live((await c.find_site(site))["id"], version)

    s = await _call(go)
    return {"site_id": s["id"], "live": s["live"]}


@server.tool(annotations=CHANGE)
async def rename_site(site: str, slug: str) -> dict[str, Any]:
    """Change a site's link name. Old links forward to the new one; visitors' saved data starts fresh there."""

    async def go(c: Client):
        return await c.set_slug((await c.find_site(site))["id"], slug.strip().lower())

    body = await _call(go)
    return {"site_id": body["site"]["id"], "slug": body["site"].get("slug"), "url": body["url"]}


@server.tool(annotations=DELETE)
async def delete_version(site: str, version: int) -> dict[str, Any]:
    """Delete one version that isn't live. Its pinned link stops working; the number is never reused."""

    async def go(c: Client):
        return await c.delete_version((await c.find_site(site))["id"], version)

    s = await _call(go)
    return {"site_id": s["id"], "remaining": [v["n"] for v in s["versions"]]}


@server.tool(annotations=DELETE)
async def unpublish_site(site: str) -> dict[str, Any]:
    """Unpublish a whole site: all its links stop working. Confirm with the user first."""

    async def go(c: Client):
        s = await c.find_site(site)
        await c.delete_site(s["id"])
        return s

    s = await _call(go)
    return {"unpublished": s["id"], "name": s["name"]}


def run() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # a line per request on stderr is noise in MCP client logs
    server.run("stdio")
