"""The spinsite CLI against the real API app (in process, through httpx's ASGI transport)."""

import io
import json
import os
import stat
import zipfile

import httpx
import pytest
from typer.testing import CliRunner

import api.index as server
from spinsite import bundle as bundle_mod
from spinsite import cli, config
from spinsite.client import Client
from test_auth import ALICE, BOB, client, sign_in  # noqa: F401  (client is a fixture)

BASE = "http://testserver"
runner = CliRunner()


@pytest.fixture
def spin(client, monkeypatch, tmp_path):
    """Run `spinsite ...` against the test server. Returns (invoke, alice's API token)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("SPINSITE_TOKEN", raising=False)
    monkeypatch.delenv("SPINSITE_URL", raising=False)
    transport = httpx.ASGITransport(app=server.app)
    monkeypatch.setattr(
        cli, "make_client", lambda base=None, token=None: Client(base or BASE, token or config.token(), transport)
    )
    sign_in(client, monkeypatch, ALICE)
    secret = client.post("/api/me/tokens", json={"name": "cli"}).json()["secret"]
    client.cookies.clear()

    def invoke(*args, input=None):
        return runner.invoke(cli.app, list(args), input=input)

    return invoke, secret


def site_dir(tmp_path, files: dict[str, str], name="mysite"):
    d = tmp_path / name
    for path, text in files.items():
        (d / path).parent.mkdir(parents=True, exist_ok=True)
        (d / path).write_text(text)
    return d


def test_login_saves_token_owner_only(spin):
    invoke, secret = spin
    r = invoke("login", "--token", secret, "--url", BASE)
    assert r.exit_code == 0, r.output
    assert "alice@example.com" in r.output
    assert config.load() == {"url": BASE, "token": secret}
    assert stat.S_IMODE(os.stat(config.path()).st_mode) == 0o600
    assert "alice@example.com" in invoke("whoami").output
    assert invoke("logout").exit_code == 0 and config.load() == {}
    r = invoke("sites")
    assert r.exit_code == 1 and "not logged in" in r.output


def test_login_refuses_bad_tokens(spin):
    invoke, _ = spin
    assert "start with srk_" in invoke("login", "--token", "edit-token", "--url", BASE).output
    r = invoke("login", "--token", "srk_nope", "--url", BASE)
    assert r.exit_code == 1 and "revoked" in r.output and config.load() == {}


def test_deploy_publish_rollback_delete(spin, tmp_path, client):
    invoke, secret = spin
    invoke("login", "--token", secret, "--url", BASE)
    d = site_dir(tmp_path, {"index.html": "v1", "css/a.css": "p{}"})

    r = invoke("deploy", str(d), "--slug", "my-site", "--json")
    assert r.exit_code == 0, r.output
    body = json.loads(r.output)
    sid = body["site"]["id"]
    assert body["site"]["name"] == "mysite" and body["site"]["slug"] == "my-site" and not body["held"]

    (d / "index.html").write_text("v2")
    r = invoke("deploy", str(d), "--site", "my-site", "--note", "second")
    assert r.exit_code == 0 and "v2" in r.output

    versions = json.loads(invoke("versions", "my-site", "--json").output)
    assert versions["live"] == 2 and [v["note"] for v in versions["versions"]] == ["", "second"]
    assert "my-site" in invoke("sites").output

    assert "live on v1" in invoke("live", "my-site", "1").output
    assert invoke("rm", "my-site", "1", "--yes").exit_code == 1  # the live version can't be deleted
    assert invoke("rm", "my-site", "2", "--yes").exit_code == 0
    assert [v["n"] for v in json.loads(invoke("versions", sid, "--json").output)["versions"]] == [1]

    assert "now at" in invoke("rename", sid, "other-name").output
    assert invoke("rm", "other-name", input="n\n").exit_code == 1  # declined
    assert invoke("rm", "other-name", "--yes").exit_code == 0
    assert json.loads(invoke("sites", "--json").output) == []


def test_cannot_touch_other_accounts_sites(spin, tmp_path, client, monkeypatch):
    invoke, secret = spin
    sign_in(client, monkeypatch, BOB)
    bobs = client.post(
        "/api/sites", files={"file": ("s.zip", bundle_mod.bundle(site_dir(tmp_path, {"index.html": "b"}))[0])}
    )
    client.cookies.clear()
    monkeypatch.setenv("SPINSITE_TOKEN", secret)
    r = invoke("deploy", str(site_dir(tmp_path, {"index.html": "x"}, "x")), "--site", bobs.json()["site"]["id"])
    assert r.exit_code == 1 and "no site" in r.output


def test_usage(spin, tmp_path, monkeypatch):
    invoke, secret = spin
    monkeypatch.setenv("SPINSITE_TOKEN", secret)
    invoke("deploy", str(site_dir(tmp_path, {"index.html": "hello"})))
    assert json.loads(invoke("usage", "--json").output)["used_bytes"] == 5


def test_bundle_skips_hidden_files_and_symlinks(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private key")
    d = site_dir(tmp_path, {"index.html": "hi", ".env": "KEY=1", ".git/config": "x", "a/b.js": "1"})
    (d / "leak.txt").symlink_to(secret)
    (d / "linkdir").symlink_to(tmp_path)
    data, count = bundle_mod.bundle(d)
    assert sorted(zipfile.ZipFile(io.BytesIO(data)).namelist()) == ["a/b.js", "index.html"] and count == 2


def test_bundle_limits(tmp_path, monkeypatch):
    with pytest.raises(bundle_mod.BundleError, match="no .html"):
        bundle_mod.bundle(site_dir(tmp_path, {"a.css": "p{}"}, "nohtml"))
    with pytest.raises(bundle_mod.BundleError, match="doesn't exist"):
        bundle_mod.bundle(tmp_path / "missing")
    monkeypatch.setattr(bundle_mod, "MAX_FILES", 1)
    with pytest.raises(bundle_mod.BundleError, match="at most 1"):
        bundle_mod.bundle(site_dir(tmp_path, {"index.html": "1", "b.html": "2"}, "many"))
    monkeypatch.setattr(bundle_mod, "MAX_FILES", 2000)
    monkeypatch.setattr(bundle_mod, "MAX_ZIP_BYTES", 10)
    with pytest.raises(bundle_mod.BundleError, match="zipped"):
        bundle_mod.bundle(site_dir(tmp_path, {"index.html": "x" * 100}, "big"))


def test_bundle_accepts_a_zip(tmp_path):
    z = tmp_path / "site.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("index.html", "hi")
    assert bundle_mod.bundle(z)[1] == 1
