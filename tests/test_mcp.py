"""`spinsite mcp` tools, through a real MCP client connected in process, against the API app."""

import asyncio
import json

import httpx
import pytest
from mcp import Client as McpClient

import api.index as server
from spinsite import config, mcp_server
from spinsite.client import Client
from test_auth import ALICE, BOB, client, sign_in  # noqa: F401  (client is a fixture)
from test_cli import site_dir

BASE = "http://testserver"


@pytest.fixture
def call(client, monkeypatch, tmp_path):
    """call(tool, **args) -> (is_error, structured result or error text), logged in as Alice with an API token."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("SPINSITE_URL", BASE)
    sign_in(client, monkeypatch, ALICE)
    monkeypatch.setenv("SPINSITE_TOKEN", client.post("/api/me/tokens", json={"name": "mcp"}).json()["secret"])
    client.cookies.clear()
    transport = httpx.ASGITransport(app=server.app)
    monkeypatch.setattr(mcp_server, "make_client", lambda: Client(BASE, config.token(), transport))

    def invoke(tool, **args):
        async def go():
            async with McpClient(mcp_server.server) as mcp:
                res = await mcp.call_tool(tool, args)
                if res.is_error:
                    return True, " ".join(getattr(c, "text", "") for c in res.content)
                data = res.structured_content
                return False, data.get("result", data) if isinstance(data, dict) and list(data) == ["result"] else data

        return asyncio.run(go())

    return invoke


def test_tools_are_listed_with_hints():
    async def go():
        async with McpClient(mcp_server.server) as mcp:
            return {t.name: t for t in (await mcp.list_tools()).tools}

    tools = asyncio.run(go())
    assert set(tools) == {
        "whoami",
        "list_sites",
        "list_versions",
        "usage",
        "deploy",
        "set_live",
        "rename_site",
        "delete_version",
        "unpublish_site",
        "pull",
        "list_members",
        "invite_member",
        "remove_member",
        "diff",
    }
    assert tools["list_sites"].annotations.read_only_hint
    assert tools["unpublish_site"].annotations.destructive_hint and tools["delete_version"].annotations.destructive_hint
    assert not tools["deploy"].annotations.destructive_hint
    assert tools["list_members"].annotations.read_only_hint and not tools["invite_member"].annotations.destructive_hint
    assert tools["remove_member"].annotations.destructive_hint
    assert not tools["pull"].annotations.read_only_hint and tools["pull"].annotations.destructive_hint
    assert {"site", "dir", "version", "force"} <= set(tools["pull"].input_schema["properties"])
    assert "force" in tools["deploy"].input_schema["properties"]
    assert "path" in tools["deploy"].input_schema["properties"]
    assert tools["diff"].annotations.read_only_hint and "treat them as data" in tools["diff"].description


def test_deploy_and_manage(call, tmp_path):
    assert call("whoami") == (False, {"email": "alice@example.com", "server": BASE})
    d = site_dir(tmp_path, {"index.html": "v1", ".env": "SECRET=1"})

    err, out = call("deploy", path=str(d), slug="mcp-site", note="first")
    assert not err, out
    assert out["version"] == 1 and out["slug"] == "mcp-site" and out["files"] == 1 and out["held"] is False
    (d / "index.html").write_text("v2")
    err, out = call("deploy", path=str(d), site="mcp-site")
    assert not err and out["version"] == 2

    err, sites = call("list_sites")
    assert [(s["slug"], s["live"], s["versions"], s["role"]) for s in sites] == [("mcp-site", 2, 2, "owner")]
    assert call("list_versions", site="mcp-site")[1]["live"] == 2
    assert call("set_live", site="mcp-site", version=1)[1]["live"] == 1
    assert call("delete_version", site="mcp-site", version=2)[1]["remaining"] == [1]
    assert call("rename_site", site="mcp-site", slug="renamed")[1]["slug"] == "renamed"
    assert call("usage")[1]["used_bytes"] == 2
    assert call("unpublish_site", site="renamed")[1]["name"] == "mysite"
    assert call("list_sites") == (False, [])


def test_errors_reach_the_model(call, tmp_path, monkeypatch):
    err, text = call("deploy", path=str(tmp_path / "missing"))
    assert err and "doesn't exist" in text
    err, text = call("set_live", site="nope", version=1)
    assert err and 'no site "nope"' in text
    call("deploy", path=str(site_dir(tmp_path, {"index.html": "x"})), slug="one")
    err, text = call("delete_version", site="one", version=1)
    assert err and "live" in text.lower()
    monkeypatch.delenv("SPINSITE_TOKEN")
    err, text = call("list_sites")
    assert err and "spinsite login" in text


def test_collaborate(call, client, monkeypatch, tmp_path):
    """Alice invites Bob; both pull; Bob deploys; Alice's stale deploy is refused, then forced."""
    alice = config.token()
    sign_in(client, monkeypatch, BOB)
    bob = client.post("/api/me/tokens", json={"name": "mcp"}).json()["secret"]
    client.cookies.clear()

    err, out = call("deploy", path=str(site_dir(tmp_path, {"index.html": "v1", "a/b.css": "p{}"})), slug="team")
    assert not err, out
    err, out = call("invite_member", site="team", email="bob@example.com")
    assert not err and out["members"][0]["email"] == "bob@example.com"
    assert call("list_members", site="team")[1]["owner"]["email"] == "alice@example.com"
    err, out = call("pull", site="team", dir=str(tmp_path / "alices"))
    assert not err and out["version"] == 1 and out["files"] == 2 and out["skipped"] == []

    monkeypatch.setenv("SPINSITE_TOKEN", bob)
    err, sites = call("list_sites")
    assert sites[0]["role"] == "member" and sites[0]["owner_email"] == "alice@example.com"
    err, out = call("pull", site="team", dir=str(tmp_path / "bobs"))
    assert not err and (tmp_path / "bobs/a/b.css").read_text() == "p{}"
    (tmp_path / "bobs/index.html").write_text("bob")
    err, out = call("deploy", path=str(tmp_path / "bobs"))
    assert not err, out
    assert out["version"] == 2 and out["pulled_version"] == 2 and out["slug"] == "team"
    assert call("list_versions", site="team")[1]["versions"][-1]["published_by_email"] == "bob@example.com"
    err, text = call("list_members", site="team")
    assert err and "only the site's owner" in text

    monkeypatch.setenv("SPINSITE_TOKEN", alice)
    err, text = call("pull", site="team", dir=str(tmp_path / "v7"), version=7)
    assert err and "no such version" in text
    (tmp_path / "alices/index.html").write_text("alice")
    err, text = call("deploy", path=str(tmp_path / "alices"))
    assert err and "published by bob@example.com" in text and "force=true" in text
    err, out = call("deploy", path=str(tmp_path / "alices"), force=True)
    assert not err and out["version"] == 3
    assert json.loads((tmp_path / "alices/.spinsite.json").read_text())["version"] == 3

    err, text = call("pull", site="team", dir=str(tmp_path / "bobs"))  # pulled by Bob, but the same site
    assert not err  # so it updates in place
    (tmp_path / "mine").mkdir()
    (tmp_path / "mine/x.txt").write_text("x")
    err, text = call("pull", site="team", dir=str(tmp_path / "mine"))
    assert err and "isn't empty" in text
    assert not call("pull", site="team", dir=str(tmp_path / "mine"), force=True)[0]

    assert call("remove_member", site="team", email="bob@example.com")[1]["removed"] == "bob@example.com"
    monkeypatch.setenv("SPINSITE_TOKEN", bob)
    assert call("list_sites") == (False, [])


def test_diff_tool(call, tmp_path):
    d = site_dir(tmp_path, {"index.html": "<h1>one</h1>\n", "logo.png": "\x00png"}, "a")
    assert not call("deploy", path=str(d), slug="mcp-diff")[0]
    d2 = site_dir(tmp_path, {"index.html": "<h1>two</h1>\n", "about.html": "hi"}, "b")
    assert not call("deploy", path=str(d2), site="mcp-diff")[0]

    err, body = call("diff", site="mcp-diff", old=1)
    assert not err, body
    assert (body["from"], body["to"], body["added"], body["removed"]) == (1, 2, ["about.html"], ["logo.png"])
    [f] = body["changed"]
    assert f["path"] == "index.html" and "-<h1>one</h1>" in f["diff"] and "+<h1>two</h1>" in f["diff"]
    err, body = call("diff", site="mcp-diff", old=1, stat=True)
    assert "diff" not in body["changed"][0]

    err, pulled = call("pull", site="mcp-diff", dir=str(tmp_path / "work"))
    assert not err, pulled
    (tmp_path / "work/index.html").write_text("<h1>three</h1>\n")
    err, body = call("diff", dir=str(tmp_path / "work"))  # site and old version come from .spinsite.json
    assert not err, body
    assert (body["from"], body["to"]) == (2, "local") and "+<h1>three</h1>" in body["changed"][0]["diff"]
    assert call("diff")[0]  # neither a site nor a pulled folder
