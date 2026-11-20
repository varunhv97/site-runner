"""The `spinsite` command: publish folders as shared sites and manage their versions.

Every command takes --json for output a script or an agent can parse. Errors go to stderr with exit code 1.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable

import typer

from . import config
from .bundle import BundleError, bundle
from .client import ApiError, Client
from .pull import PullError, after_deploy, default_entry, pull as pull_version, read_state, stale_message, target_site

app = typer.Typer(
    help="Publish and manage your Site Runner sites. Create a token at <site>/app → API tokens, then `spinsite login`.",
    no_args_is_help=True,
    add_completion=False,
)

JsonOpt = Annotated[bool, typer.Option("--json", help="Print JSON instead of text.")]


def make_client(base_url: str | None = None, token: str | None = None) -> Client:
    return Client(base_url or config.base_url(), token or config.token())


def _fail(message: str) -> None:
    typer.secho(f"error: {message}", err=True, fg=typer.colors.RED)
    raise typer.Exit(1)


def run(fn: Callable[[Client], Awaitable[Any]], needs_token: bool = True) -> Any:
    if needs_token and not config.token():
        _fail("not logged in. Create a token at <site>/app → API tokens, then run `spinsite login`.")

    async def go():
        async with make_client() as client:
            return await fn(client)

    try:
        return asyncio.run(go())
    except (ApiError, BundleError, PullError) as e:
        _fail(str(e))


def out(as_json: bool, data: Any, text: str) -> None:
    typer.echo(json.dumps(data, indent=2) if as_json else text)


def _when(t: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "-"


def _size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    return f"{n / 1024:.0f}KB" if n < 1024 * 1024 else f"{n / (1024 * 1024):.1f}MB"


def _held_line(body: dict) -> str:
    if not body.get("held"):
        return ""
    return "\nHeld for a safety review: the links show a notice until an admin approves it. " + ", ".join(
        body.get("reasons", [])
    )


# ---- account ----


@app.command()
def login(
    token: Annotated[str, typer.Option(help="The API token (srk_...). Asked for when left out.")] = "",
    url: Annotated[str, typer.Option(help="The Site Runner server.")] = "",
):
    """Save an API token (made at <site>/app → API tokens) and check it works."""
    base = (url or config.base_url()).rstrip("/")
    if not token:
        typer.echo(f"Create a token at {base}/app → API tokens.")
        token = typer.prompt("API token", hide_input=True).strip()
    if not token.startswith("srk_"):
        _fail("that isn't an account API token (they start with srk_)")

    async def check(_: Client):
        async with make_client(base, token) as c:
            return await c.me()

    user = run(check, needs_token=False)
    if not user:
        _fail("the server didn't accept that token")
    config.save({"url": base, "token": token})
    typer.echo(f"Logged in to {base} as {user['email']}. Token saved in {config.path()}")


@app.command()
def logout():
    """Forget the saved token (revoke it at <site>/app → API tokens to stop it working)."""
    typer.echo("Logged out." if config.clear() else "Not logged in.")


@app.command()
def whoami(as_json: JsonOpt = False):
    """The account the token acts as."""
    user = run(lambda c: c.me())
    out(as_json, {"url": config.base_url(), **(user or {})}, f"{user['email']} on {config.base_url()}")


@app.command()
def usage(as_json: JsonOpt = False):
    """Storage your shared sites use, against your quota."""
    u = run(lambda c: c.usage())
    pct = 100 * u["used_bytes"] / u["quota_bytes"] if u["quota_bytes"] else 0
    out(as_json, u, f"{_size(u['used_bytes'])} of {_size(u['quota_bytes'])} ({pct:.0f}%)")


# ---- sites ----


@app.command()
def sites(as_json: JsonOpt = False):
    """Your shared sites, then the sites you're a member of (with their owner)."""
    found = run(lambda c: c.sites())
    lines = [
        f"{s.get('slug') or s['id']:<24} {s['name'][:30]:<30} live v{s['live'] or '-':<4} {s['url']}"
        + (f"  member (owner {s.get('owner_email') or '?'})" if s.get("role") == "member" else "")
        for s in found
    ]
    out(as_json, found, "\n".join(lines) or "No shared sites yet. Publish one with `spinsite deploy <folder>`.")


@app.command()
def versions(site: Annotated[str, typer.Argument(help="Site id, link name or name.")], as_json: JsonOpt = False):
    """A site's versions (* = live)."""
    s = run(lambda c: c.find_site(site))
    lines = [
        f"{'*' if v['n'] == s['live'] else ' '} v{v['n']:<4} {_when(v['created_at'])}  {v['files']:>4} files "
        f"{_size(v['size']):>8}  {v.get('published_by_email') or '':<24} {v['note']}"
        for v in reversed(s["versions"])
    ]
    out(as_json, s, "\n".join(lines))


@app.command()
def deploy(
    path: Annotated[str, typer.Argument(help="A folder or a .zip with the site's files.")] = ".",
    site: Annotated[
        str, typer.Option(help="Publish a new version of this site (default: the site a pulled folder came from).")
    ] = "",
    name: Annotated[str, typer.Option(help="Name for a new site (default: the folder's name).")] = "",
    slug: Annotated[str, typer.Option(help="Link name for a new site: <slug>.sites.spinsite.app.")] = "",
    note: Annotated[str, typer.Option(help="A note for this version.")] = "",
    entry: Annotated[str, typer.Option(help="The page to open on, if not index.html.")] = "",
    force: Annotated[
        bool, typer.Option("--force", help="Publish even if someone published a newer version since your pull.")
    ] = False,
    as_json: JsonOpt = False,
):
    """Publish a folder: a new site, or a new live version of one (--site, or the site a pulled folder came from).

    From a pulled folder, refuses when the site's live version is newer than the one pulled (--force skips this)."""
    try:
        zipped, count = bundle(path)
    except BundleError as e:
        _fail(str(e))
    folder = Path(path)
    state = read_state(folder) if folder.is_dir() else None

    async def go(c: Client):
        target = await target_site(c, site, state)
        if target:
            if not force and (stale := stale_message(target, state, path)):
                raise PullError(stale.replace("use force", "use --force"))
            chosen = default_entry(folder, state, entry) if state and state.get("site_id") == target["id"] else entry
            return await c.add_version(target["id"], zipped, note=note, entry=chosen), True
        default_name = name or _folder_name(path)
        return await c.create_site(zipped, name=default_name, slug=slug, note=note, entry=entry), False

    body, existing = run(go)
    s = body["site"]
    text = f"{'Published' if existing else 'Shared'} {s['name']} v{s['live']} ({count} files)\n{body['url']}"
    if folder.is_dir() and after_deploy(folder, state, body):
        body["pulled_version"] = s["live"]
    if not existing:
        text += f"\nNext time: spinsite deploy {path} --site {s.get('slug') or s['id']}"
    out(as_json, body, text + _held_line(body))


@app.command()
def pull(
    site: Annotated[str, typer.Argument(help="Site id, link name or name (yours or one you're a member of).")],
    folder: Annotated[str, typer.Argument(help="Where to put the files (default: ./<link name or id>).")] = "",
    version: Annotated[int | None, typer.Option(help="The version to pull (default: the live one).")] = None,
    force: Annotated[bool, typer.Option("--force", help="Write into a folder that isn't empty.")] = False,
    as_json: JsonOpt = False,
):
    """Download a version of a site into a folder, to edit and `spinsite deploy` back.

    Hidden files and unsafe paths are skipped. The folder gets a .spinsite.json naming the site and version."""

    async def go(c: Client):
        return await pull_version(c, await c.find_site(site), folder or None, version, force)

    body = run(go)
    text = f"Pulled {body['name']} v{body['version']} ({body['files']} files) into {body['dir']}"
    if body["skipped"]:
        text += "\nSkipped (hidden or unsafe paths): " + ", ".join(body["skipped"])
        typer.secho(f"warning: skipped {len(body['skipped'])} hidden or unsafe paths", err=True, fg="yellow")
    text += f"\nEdit, then: spinsite deploy {body['dir']}"
    out(as_json, body, text)


def _folder_name(path: str) -> str:
    p = Path(path).resolve()
    return (p.stem if p.is_file() else p.name)[:100] or "site"


@app.command()
def live(
    site: Annotated[str, typer.Argument(help="Site id, link name or name.")],
    version: Annotated[int, typer.Argument(help="The version to make live (roll back or forward).")],
    as_json: JsonOpt = False,
):
    """Make a version live: the site's link shows it from now on."""

    async def go(c: Client):
        return await c.set_live((await c.find_site(site))["id"], version)

    s = run(go)
    out(as_json, s, f"{s['name']} is live on v{s['live']}")


@app.command()
def rename(
    site: Annotated[str, typer.Argument(help="Site id, link name or name.")],
    slug: Annotated[str, typer.Argument(help="The new link name.")],
    as_json: JsonOpt = False,
):
    """Change a site's link name. Old links forward; visitors' saved data starts fresh on the new address."""

    async def go(c: Client):
        return await c.set_slug((await c.find_site(site))["id"], slug.strip().lower())

    body = run(go)
    out(as_json, body, f"{body['site']['name']} is now at {body['url']}")


@app.command()
def rm(
    site: Annotated[str, typer.Argument(help="Site id, link name or name.")],
    version: Annotated[int | None, typer.Argument(help="Delete only this version (not the live one).")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
    as_json: JsonOpt = False,
):
    """Delete a version (its pinned link stops working), or unpublish the whole site."""
    s = run(lambda c: c.find_site(site))
    what = f"v{version} of {s['name']}" if version else f"{s['name']} (all links stop working)"
    if not yes and not typer.confirm(f"Delete {what}?"):
        raise typer.Exit(1)

    async def go(c: Client):
        if version:
            return await c.delete_version(s["id"], version)
        await c.delete_site(s["id"])
        return {"ok": True}

    out(as_json, run(go), f"Deleted {what}")


# ---- members ----


def _members_text(body: dict) -> str:
    lines = [f"owner    {body['owner'].get('email') or '-'}"]
    lines += [f"{m['status']:<8} {m['email']}" for m in body["members"]]
    return "\n".join(lines) + f"\n{len(body['members'])} of {body['max_members']} members"


@app.command()
def members(site: Annotated[str, typer.Argument(help="Site id, link name or name.")], as_json: JsonOpt = False):
    """Who can publish to and pull from one of your sites (owner only). Pending = hasn't signed in yet."""

    async def go(c: Client):
        return await c.members((await c.find_site(site))["id"])

    body = run(go)
    out(as_json, body, _members_text(body))


@app.command()
def invite(
    site: Annotated[str, typer.Argument(help="Site id, link name or name.")],
    email: Annotated[str, typer.Argument(help="Their Google account's email.")],
    as_json: JsonOpt = False,
):
    """Let a Google account publish to and pull from one of your sites. Latest publish wins; there's no merging."""

    async def go(c: Client):
        return await c.invite((await c.find_site(site))["id"], email)

    body = run(go)
    out(
        as_json,
        body,
        f"Invited {email.strip().lower()}. They can now `spinsite pull` and deploy it.\n" + _members_text(body),
    )


@app.command("remove-member")
def remove_member(
    site: Annotated[str, typer.Argument(help="Site id, link name or name.")],
    email: Annotated[str, typer.Argument(help="The member's email (your own to leave a site).")],
    as_json: JsonOpt = False,
):
    """Remove a member from one of your sites, or leave a site you're a member of (with your own email)."""

    async def go(c: Client):
        s = await c.find_site(site)
        await c.remove_member(s["id"], email)
        return s

    s = run(go)
    out(
        as_json, {"ok": True, "site_id": s["id"], "removed": email.strip().lower()}, f"Removed {email} from {s['name']}"
    )


@app.command()
def mcp():
    """Run as an MCP server over stdio (for Claude Code, Claude Desktop, Cursor...). Uses the same login."""
    from .mcp_server import run as run_mcp  # the MCP SDK loads only for this command

    run_mcp()


def main() -> None:
    app()
