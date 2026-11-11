"""`spinsite mcp` tools, through a real MCP client connected in process, against the API app."""

import asyncio

import httpx
import pytest
from mcp import Client as McpClient

import api.index as server
from spinsite import config, mcp_server
from spinsite.client import Client
from test_auth import ALICE, client, sign_in  # noqa: F401  (client is a fixture)
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
    }
    assert tools["list_sites"].annotations.read_only_hint
    assert tools["unpublish_site"].annotations.destructive_hint and tools["delete_version"].annotations.destructive_hint
    assert not tools["deploy"].annotations.destructive_hint
    assert "path" in tools["deploy"].input_schema["properties"]


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
    assert [(s["slug"], s["live"], s["versions"]) for s in sites] == [("mcp-site", 2, 2)]
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
