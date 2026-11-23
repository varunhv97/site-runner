"""`spinsite pull`, deploying from a pulled folder (stale check) and the members commands, against the API app."""

import hashlib
import json

import pytest

from spinsite import pull as pull_mod
from spinsite.bundle import site_files
from spinsite.client import Client
from test_auth import ALICE, BOB, client, sign_in  # noqa: F401  (client is a fixture)
from test_cli import site_dir, spin  # noqa: F401  (spin is a fixture)


@pytest.fixture
def two(spin, client, monkeypatch, tmp_path):
    """(invoke, as_alice, as_bob): switch the CLI between Alice's and Bob's API tokens. Runs in tmp_path."""
    invoke, alice = spin
    sign_in(client, monkeypatch, BOB)
    bob = client.post("/api/me/tokens", json={"name": "cli"}).json()["secret"]
    client.cookies.clear()
    monkeypatch.chdir(tmp_path)
    return (
        invoke,
        lambda: monkeypatch.setenv("SPINSITE_TOKEN", alice),
        lambda: monkeypatch.setenv("SPINSITE_TOKEN", bob),
    )


def ok(r):
    assert r.exit_code == 0, r.output
    return r


def shared_site(invoke, tmp_path, slug="team", files=None):
    d = site_dir(tmp_path, files or {"index.html": "v1", "css/a.css": "p{}", "img/x.txt": "p{}"}, "src")
    return json.loads(ok(invoke("deploy", str(d), "--slug", slug, "--json")).output)["site"]["id"]


def test_pull_writes_files_and_state(two, tmp_path):
    invoke, as_alice, _ = two
    as_alice()
    sid = shared_site(invoke, tmp_path)

    r = ok(invoke("pull", "team", "--json"))  # default folder: ./<link name>
    body = json.loads(r.output)
    assert body["version"] == 1 and body["files"] == 3 and body["skipped"] == [] and body["dir"] == "team"
    assert (tmp_path / "team/index.html").read_text() == "v1"
    assert (tmp_path / "team/css/a.css").read_text() == "p{}" and (tmp_path / "team/img/x.txt").read_text() == "p{}"
    state = json.loads((tmp_path / "team/.spinsite.json").read_text())
    assert state["site_id"] == sid and state["version"] == 1 and state["entry"] == "index.html"
    assert state["url"].endswith("/s/team/")
    assert [f.as_posix() for f in site_files(tmp_path / "team")] == ["index.html", "css/a.css", "img/x.txt"]

    # a fresh pull of the same site updates the folder in place; another site's folder is refused without --force
    ok(invoke("deploy", str(site_dir(tmp_path, {"index.html": "v2"}, "src")), "--site", "team"))
    assert "v2" in ok(invoke("pull", sid, "team")).output and (tmp_path / "team/index.html").read_text() == "v2"
    ok(invoke("pull", "team", "old", "--version", "1"))
    assert (tmp_path / "old/index.html").read_text() == "v1"
    assert json.loads((tmp_path / "old/.spinsite.json").read_text())["version"] == 1

    other = site_dir(tmp_path, {"notes.txt": "mine"}, "other")
    r = invoke("pull", "team", str(other))
    assert r.exit_code == 1 and "isn't empty" in r.output and not (other / "index.html").exists()
    ok(invoke("pull", "team", str(other), "--force"))
    assert (other / "index.html").read_text() == "v2" and (other / "notes.txt").read_text() == "mine"

    assert "no such version" in invoke("pull", "team", "x", "--version", "9").output
    assert 'no site "nope"' in invoke("pull", "nope").output


def test_pull_skips_hidden_and_unsafe_paths(two, tmp_path, monkeypatch):
    invoke, as_alice, _ = two
    as_alice()
    shared_site(invoke, tmp_path, files={"index.html": "safe"})
    digest = hashlib.sha256(b"safe").hexdigest()
    bad = [
        ".env",
        ".git/config",
        "a/.hidden/x.html",
        "../escape.html",
        "a/../../escape2.html",
        "/etc/evil.html",
        "a\\b.html",
        "C:/evil.html",
        "a//b.html",
        "./a.html",
        "a/./b.html",
        "tab\there.html",
        "",
    ]
    real = Client.version_files

    async def malicious(self, site_id, n):  # a compromised or buggy server
        body = await real(self, site_id, n)
        return body | {"files": body["files"] | {p: digest for p in bad} | {"ok/fine.html": "not-a-digest"}}

    monkeypatch.setattr(Client, "version_files", malicious)
    (tmp_path / "outside").mkdir()
    (tmp_path / "dest").mkdir()
    (tmp_path / "dest/link").symlink_to(tmp_path / "outside")  # an existing symlink must not be followed out
    bad.append("link/evil.html")
    r = ok(invoke("pull", "team", "dest", "--force", "--json"))
    body = json.loads(r.stdout)
    assert body["files"] == 1 and sorted(body["skipped"]) == sorted(bad + ["ok/fine.html"])
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.html"))
    assert written == ["dest/index.html", "src/index.html"]
    assert sorted(p.name for p in (tmp_path / "dest").iterdir()) == [".spinsite.json", "index.html", "link"]
    assert list((tmp_path / "outside").iterdir()) == []
    assert "warning: skipped 15" in invoke("pull", "team", "dest").output


def test_safe_target(tmp_path):
    assert pull_mod.safe_target(tmp_path, "a/b c.html") == (tmp_path / "a/b c.html").resolve()
    for p in ["..", ".", "a/..", "a/", "/", ".spinsite.json", "a\x00b", "\\server\\share\\x.html"]:
        assert pull_mod.safe_target(tmp_path, p) is None, p


def test_pull_checks_downloads(two, tmp_path, monkeypatch):
    invoke, as_alice, _ = two
    as_alice()
    shared_site(invoke, tmp_path)

    async def tampered(self, site_id, digest):
        return b"not what was asked for"

    monkeypatch.setattr(Client, "file_bytes", tampered)
    r = invoke("pull", "team")
    assert r.exit_code == 1 and "checksum" in r.output and not (tmp_path / "team").exists()


def test_member_pulls_and_deploys_stale_refused_then_forced(two, tmp_path):
    invoke, as_alice, as_bob = two
    as_alice()
    sid = shared_site(invoke, tmp_path)
    assert "no site" in (as_bob() or invoke("pull", "team")).output  # not a member yet

    as_alice()
    r = ok(invoke("invite", "team", "Bob@Example.com"))
    assert "bob@example.com" in r.output and "1 of 10 members" in r.output
    members = json.loads(ok(invoke("members", "team", "--json")).output)
    assert members["owner"]["email"] == "alice@example.com" and members["members"][0]["email"] == "bob@example.com"
    assert "already a member" in invoke("invite", "team", "bob@example.com").output

    # nothing is shared until Bob accepts
    as_bob()
    assert "no site" in invoke("pull", "team").output
    r = ok(invoke("invites"))
    assert "team" in r.output and "from alice@example.com" in r.output
    assert "no invitation" in invoke("accept", "nope").output
    assert "member of" in ok(invoke("accept", "team")).output
    assert ok(invoke("invites")).output.strip() == "No invitations."

    # both pull v1
    assert "member (owner alice@example.com)" in ok(invoke("sites")).output
    assert json.loads(ok(invoke("sites", "--json")).output)[0]["role"] == "member"
    ok(invoke("pull", "team", "bobs"))
    as_alice()
    ok(invoke("pull", "team", "alices"))

    # Bob deploys from his folder: no --site needed, and his .spinsite.json moves to v2
    as_bob()
    (tmp_path / "bobs/index.html").write_text("bob's v2")
    r = ok(invoke("deploy", "bobs", "--json"))
    body = json.loads(r.output)
    assert body["site"]["id"] == sid and body["site"]["live"] == 2 and body["pulled_version"] == 2
    assert json.loads((tmp_path / "bobs/.spinsite.json").read_text())["version"] == 2
    assert "bob@example.com" in ok(invoke("versions", "team")).output

    # Alice's folder is still at v1: refused, naming Bob and suggesting a pull
    as_alice()
    (tmp_path / "alices/index.html").write_text("alice's v2")
    r = invoke("deploy", "alices")
    assert r.exit_code == 1
    assert "live on v2, published by bob@example.com" in r.output and "spinsite pull team alices" in r.output
    assert "--force" in r.output
    assert json.loads(ok(invoke("versions", "team", "--json")).output)["live"] == 2  # nothing was published

    r = ok(invoke("deploy", "alices", "--force", "--note", "mine wins"))
    assert "v3" in r.output and json.loads((tmp_path / "alices/.spinsite.json").read_text())["version"] == 3

    # after a pull Bob can deploy again (in place, his folder is a pull of the same site)
    as_bob()
    assert invoke("deploy", "bobs").exit_code == 1
    ok(invoke("pull", "team", "bobs"))
    assert (tmp_path / "bobs/index.html").read_text() == "alice's v2"
    ok(invoke("deploy", "bobs"))

    # Bob can't manage members; he can leave
    assert "only the site's owner" in invoke("members", "team").output
    assert "only the site's owner" in invoke("invite", "team", "carol@example.com").output
    ok(invoke("remove-member", "team", "bob@example.com"))
    assert json.loads(ok(invoke("sites", "--json")).output) == []
    assert invoke("deploy", "bobs").exit_code == 1  # no longer a member: the pulled site isn't his


def test_deploy_from_pulled_folder_targets_its_site(two, tmp_path):
    invoke, as_alice, _ = two
    as_alice()
    first = shared_site(invoke, tmp_path, slug="first")
    second = shared_site(invoke, tmp_path, slug="second")
    ok(invoke("pull", "first", "work"))
    (tmp_path / "work/index.html").write_text("edited")

    # an explicit --site wins (and no stale check / state update for a different site)
    body = json.loads(ok(invoke("deploy", "work", "--site", "second", "--json")).output)
    assert body["site"]["id"] == second and "pulled_version" not in body
    body = json.loads(ok(invoke("deploy", "work", "--json")).output)
    assert body["site"]["id"] == first and body["site"]["live"] == 2

    # deploy defaults to the current folder
    import os

    os.chdir(tmp_path / "work")
    assert json.loads(ok(invoke("deploy", "--json")).output)["site"]["live"] == 3

    # the pulled start page is kept when deploying without --entry
    os.chdir(tmp_path)
    src = site_dir(tmp_path, {"a.html": "a", "home.html": "h"}, "pages-src")
    ok(invoke("deploy", str(src), "--slug", "pages", "--entry", "home.html"))
    ok(invoke("pull", "pages", "pages"))
    assert json.loads((tmp_path / "pages/.spinsite.json").read_text())["entry"] == "home.html"
    (tmp_path / "pages/a.html").write_text("a2")
    body = json.loads(ok(invoke("deploy", "pages", "--json")).output)
    assert body["site"]["versions"][-1]["entry"] == "home.html"
    assert json.loads((tmp_path / "pages/.spinsite.json").read_text()) | {"url": None} == {
        "site_id": body["site"]["id"],
        "url": None,
        "version": 2,
        "entry": "home.html",
        "server": "http://testserver",
    }


def test_owner_removes_member(two, tmp_path):
    invoke, as_alice, as_bob = two
    as_alice()
    shared_site(invoke, tmp_path)
    ok(invoke("invite", "team", "bob@example.com"))
    ok(invoke("invite", "team", "carol@example.com"))
    out = ok(invoke("members", "team")).output
    assert "pending  carol@example.com  (invited, hasn't accepted yet)" in out
    r = json.loads(ok(invoke("remove-member", "team", "Carol@example.com", "--json")).output)
    assert r == {"ok": True, "site_id": r["site_id"], "removed": "carol@example.com"}
    assert "no such member" in invoke("remove-member", "team", "carol@example.com").output
    as_bob()
    ok(invoke("accept", "team"))
    ok(invoke("pull", "team", "b"))
    as_alice()
    ok(invoke("remove-member", "team", "bob@example.com"))
    as_bob()
    assert "no site" in invoke("pull", "team", "c").output


def test_decline_an_invitation(two, tmp_path):
    invoke, as_alice, as_bob = two
    as_alice()
    sid = shared_site(invoke, tmp_path)
    ok(invoke("invite", "team", "bob@example.com"))
    as_bob()
    [inv] = json.loads(ok(invoke("invites", "--json")).output)
    assert (inv["site_id"], inv["slug"], inv["owner_email"]) == (sid, "team", "alice@example.com")
    assert json.loads(ok(invoke("decline", sid, "--json")).output) == {"ok": True, "site_id": sid}
    assert json.loads(ok(invoke("invites", "--json")).output) == []
    assert "no site" in invoke("pull", "team").output


def test_owned_site_wins_over_a_member_site_with_the_same_name(two, tmp_path):
    """Bob invites Alice to a site whose link name is the display name of one of Alice's sites, hoping her
    `deploy --site portfolio` lands on his site."""
    invoke, as_alice, as_bob = two
    as_alice()
    d = site_dir(tmp_path, {"index.html": "alice"}, "portfolio")
    mine = json.loads(ok(invoke("deploy", str(d), "--json")).output)["site"]
    assert mine["name"] == "portfolio" and mine["slug"] is None
    as_bob()
    trap = shared_site(invoke, tmp_path, slug="portfolio")
    named = json.loads(
        ok(
            invoke("deploy", str(site_dir(tmp_path, {"index.html": "b"}, "bob-src")), "--name", "Notes", "--json")
        ).output
    )["site"]
    ok(invoke("invite", "portfolio", "alice@example.com"))
    ok(invoke("invite", named["id"], "alice@example.com"))
    as_alice()
    ok(invoke("accept", "portfolio"))
    ok(invoke("accept", named["id"]))

    body = json.loads(ok(invoke("deploy", str(d), "--site", "portfolio", "--json")).output)
    assert body["site"]["id"] == mine["id"]  # her own site, by display name, beats his link name
    assert json.loads(ok(invoke("versions", trap, "--json")).output)["id"] == trap  # his by exact id still works
    r = invoke("versions", "Notes")  # a member site is never matched by display name
    assert r.exit_code == 1 and "shared with you" in r.output
    assert json.loads(ok(invoke("versions", named["id"], "--json")).output)["id"] == named["id"]


def test_pull_points_out_files_agents_and_tools_load(two, tmp_path):
    invoke, as_alice, _ = two
    as_alice()
    files = {
        "index.html": "x",
        "CLAUDE.md": "do things",
        "package.json": "{}",
        "sub/AGENTS.md": "a",
        "sub/Makefile": "m",
    }
    shared_site(invoke, tmp_path, files=files)
    r = ok(invoke("pull", "team", "--json"))
    assert json.loads(r.stdout)["review_before_running"] == ["CLAUDE.md", "package.json", "sub/AGENTS.md"]
    assert "CLAUDE.md, package.json, sub/AGENTS.md" in r.stderr


def test_invite_link_commands(two, tmp_path, client, monkeypatch):
    invoke, as_alice, as_bob = two
    as_alice()
    sid = shared_site(invoke, tmp_path)
    r = ok(invoke("invite-link", "team"))
    url = r.output.splitlines()[0]
    assert url.startswith("http://testserver/join#srj_") and "works once" in r.output
    made = json.loads(ok(invoke("invite-link", "team", "--json")).output)
    assert set(made) == {"link", "url"} and set(made["link"]) == {"id", "created_at", "expires_at"}
    assert "(+2 unused invite links)" in ok(invoke("members", "team")).output

    listed = ok(invoke("invite-links", "team")).output
    assert made["link"]["id"] in listed and "expires in 60 min" in listed and "srj_" not in listed
    assert "Revoked" in ok(invoke("revoke-link", "team", made["link"]["id"])).output
    assert "no such invite link" in invoke("revoke-link", "team", made["link"]["id"]).output

    sign_in(client, monkeypatch, BOB)  # Bob opens the link in the browser
    assert client.post("/api/invite-links/redeem", json={"code": url.split("#", 1)[1]}).status_code == 200
    client.cookies.clear()
    assert "used by bob@example.com" in ok(invoke("invite-links", "team")).output
    assert json.loads(ok(invoke("invite-links", "team", "--json")).output)["links"] == []
    as_bob()
    assert "only the site's owner" in invoke("invite-link", sid).output
