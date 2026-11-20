import sqlite3

from _core import members
from test_api_tokens import bearer, new_token, upload
from test_auth import ALICE, BOB, client, sign_in  # noqa: F401  (client is a fixture)

CAROL = {"sub": "333", "email": "carol@example.com", "name": "Carol"}
ADMIN = {"sub": "999", "email": "admin@example.com", "name": "Admin"}


def alices_site(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    r = upload(client, {})
    assert r.status_code == 200, r.text
    return r.json()["site"]["id"]


def invite(client, sid, email, **kw):
    return client.post(f"/api/sites/{sid}/members", json={"email": email}, **kw)


def test_invite_list_and_remove(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    r = invite(client, sid, "  Bob@Example.com ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["owner"] == {"email": "alice@example.com"} and body["max_members"] == members.MAX_MEMBERS
    assert [(m["email"], m["status"]) for m in body["members"]] == [("bob@example.com", "pending")]
    assert isinstance(body["members"][0]["created_at"], int)
    assert client.get(f"/api/sites/{sid}/members").json() == body

    assert invite(client, sid, "bob@example.com").status_code == 409  # already a member
    assert invite(client, sid, "ALICE@example.com").status_code == 400  # the owner
    assert invite(client, sid, "not an email").status_code == 400
    assert invite(client, sid, "carol@example.com", headers={"Origin": "https://evil.example"}).status_code == 403

    assert client.delete(f"/api/sites/{sid}/members/Bob@example.com").status_code == 200
    assert client.get(f"/api/sites/{sid}/members").json()["members"] == []
    assert client.delete(f"/api/sites/{sid}/members/bob@example.com").status_code == 404


def test_at_most_ten_members(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    for i in range(members.MAX_MEMBERS):
        assert invite(client, sid, f"user{i}@example.com").status_code == 200
    r = invite(client, sid, "one-more@example.com")
    assert r.status_code == 409 and "10 members" in r.json()["error"]


def test_only_the_owner_manages_members_and_a_member_can_leave(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    invite(client, sid, "bob@example.com")
    invite(client, sid, "carol@example.com")
    client.cookies.clear()
    assert client.get(f"/api/sites/{sid}/members").status_code == 401
    assert invite(client, sid, "dave@example.com").status_code == 401

    sign_in(client, monkeypatch, BOB)  # a member, but not the owner
    assert client.get(f"/api/sites/{sid}/members").status_code == 403
    assert invite(client, sid, "dave@example.com").status_code == 403
    assert client.delete(f"/api/sites/{sid}/members/carol@example.com").status_code == 403
    assert client.delete(f"/api/sites/{sid}/members/bob@example.com").status_code == 200  # leaves

    sign_in(client, monkeypatch, ALICE)
    assert [m["email"] for m in client.get(f"/api/sites/{sid}/members").json()["members"]] == ["carol@example.com"]


def test_pending_invite_is_accepted_on_sign_in(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    invite(client, sid, "bob@example.com")
    sign_in(client, monkeypatch, BOB)
    sign_in(client, monkeypatch, ALICE)
    assert client.get(f"/api/sites/{sid}/members").json()["members"][0]["status"] == "accepted"
    # an account that has signed in before is accepted straight away
    sign_in(client, monkeypatch, CAROL)
    sign_in(client, monkeypatch, ALICE)
    statuses = [m["status"] for m in invite(client, sid, "carol@example.com").json()["members"]]
    assert statuses == ["accepted", "accepted"]


def test_api_token_owner_manages_members(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    h = bearer(new_token(client, monkeypatch, ALICE)["secret"])
    assert invite(client, sid, "bob@example.com", headers=h).status_code == 200
    assert len(client.get(f"/api/sites/{sid}/members", headers=h).json()["members"]) == 1


def test_unpublished_site_and_banned_owner(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    sid = alices_site(client, monkeypatch)
    other = upload(client, {}).json()["site"]["id"]
    assert client.delete(f"/api/sites/{other}").status_code == 200
    assert invite(client, other, "bob@example.com").status_code == 404
    assert client.get(f"/api/sites/{other}/members").status_code == 404

    sign_in(client, monkeypatch, ADMIN)
    assert client.post("/api/admin/bans", json={"email": "alice@example.com"}).status_code == 200
    sign_in(client, monkeypatch, ALICE)
    assert invite(client, sid, "bob@example.com").status_code == 403


def test_invites_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(members, "INVITES_PER_HOUR", 2)
    sid = alices_site(client, monkeypatch)
    assert invite(client, sid, "a@example.com").status_code == 200
    assert invite(client, sid, "b@example.com").status_code == 200
    assert invite(client, sid, "c@example.com").status_code == 429


def test_publisher_is_recorded(client, monkeypatch, tmp_path):
    sid = alices_site(client, monkeypatch)
    body = upload(client, {}).json()
    client.cookies.clear()
    assert (
        upload(client, bearer(body["edit_token"]), url=f"/api/sites/{body['site']['id']}/versions").status_code == 200
    )
    rows = sqlite3.connect(tmp_path / "t.db").execute("select site_id, n, published_by from versions order by rowid")
    assert rows.fetchall() == [(sid, 1, "111"), (body["site"]["id"], 1, "111"), (body["site"]["id"], 2, None)]


# ---- access: members publish, make live, change the start page and pull; the rest stays the owner's ----


def shared_with_bob(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    assert invite(client, sid, "bob@example.com").status_code == 200
    sign_in(client, monkeypatch, BOB)
    return sid


def test_member_publishes_makes_live_changes_entry_and_pulls(client, monkeypatch):
    sid = shared_with_bob(client, monkeypatch)
    r = upload(client, {}, {"index.html": "v2", "about.html": "a"}, url=f"/api/sites/{sid}/versions")
    assert r.status_code == 200, r.text
    assert r.json()["site"]["live"] == 2
    assert client.post(f"/api/sites/{sid}/live", json={"version": 1}).json()["site"]["live"] == 1
    assert client.post(f"/api/sites/{sid}/versions/2/entry", json={"entry": "about.html"}).status_code == 200
    pulled = client.get(f"/api/sites/{sid}/versions/2/files").json()
    assert pulled["entry"] == "about.html" and set(pulled["files"]) == {"index.html", "about.html"}
    blob = client.get(f"/api/sites/{sid}/files/{pulled['files']['about.html']}")
    assert blob.status_code == 200 and blob.content == b"a"
    assert client.get(f"/api/sites/{sid}/hold").json() == {"held": False}


def test_publish_based_on_an_old_version_is_refused(client, monkeypatch):
    sid = shared_with_bob(client, monkeypatch)
    url = f"/api/sites/{sid}/versions"
    assert upload(client, {}, {"index.html": "bob"}, url=url, base_version="1").json()["site"]["live"] == 2
    sign_in(client, monkeypatch, ALICE)  # Alice also started from v1, but Bob's v2 went live first
    r = upload(client, {}, {"index.html": "alice"}, url=url, base_version="1")
    assert r.status_code == 409 and "v2 went live after v1" in r.json()["error"]
    assert upload(client, {}, {"index.html": "alice"}, url=url, base_version="2").json()["site"]["live"] == 3
    assert upload(client, {}, {"index.html": "forced"}, url=url).json()["site"]["live"] == 4  # no base: replaces


def test_member_is_refused_owner_only_routes(client, monkeypatch):
    sid = shared_with_bob(client, monkeypatch)
    assert upload(client, {}, url=f"/api/sites/{sid}/versions").status_code == 200
    assert client.post(f"/api/sites/{sid}/slug", json={"slug": "bobs-name"}).status_code == 403
    assert client.delete(f"/api/sites/{sid}/versions/1").status_code == 403
    assert client.delete(f"/api/sites/{sid}").status_code == 403
    assert client.post(f"/api/sites/{sid}/token").status_code == 403
    assert client.post(f"/api/sites/{sid}/claim").status_code == 403
    sign_in(client, monkeypatch, ALICE)
    site = client.get(f"/api/sites/{sid}").json()["site"]
    assert site["slug"] is None and [v["n"] for v in site["versions"]] == [1, 2]


def test_non_member_is_refused(client, monkeypatch):
    sid = shared_with_bob(client, monkeypatch)
    digest = next(iter(client.get(f"/api/sites/{sid}/versions/1/files").json()["files"].values()))
    sign_in(client, monkeypatch, CAROL)
    assert upload(client, {}, url=f"/api/sites/{sid}/versions").status_code == 403
    assert client.post(f"/api/sites/{sid}/live", json={"version": 1}).status_code == 403
    assert client.post(f"/api/sites/{sid}/versions/1/entry", json={"entry": "index.html"}).status_code == 403
    assert client.get(f"/api/sites/{sid}/versions/1/files").status_code == 403
    assert client.get(f"/api/sites/{sid}/files/{digest}").status_code == 403
    assert client.get(f"/api/sites/{sid}/hold").status_code == 403
    assert client.get("/api/me/sites").json()["sites"] == []


def test_removed_member_loses_access(client, monkeypatch):
    sid = shared_with_bob(client, monkeypatch)
    assert [s["id"] for s in client.get("/api/me/sites").json()["sites"]] == [sid]
    sign_in(client, monkeypatch, ALICE)
    assert client.delete(f"/api/sites/{sid}/members/bob@example.com").status_code == 200
    sign_in(client, monkeypatch, BOB)
    assert upload(client, {}, url=f"/api/sites/{sid}/versions").status_code == 403
    assert client.get(f"/api/sites/{sid}/versions/1/files").status_code == 403
    assert client.get("/api/me/sites").json()["sites"] == []


def test_banned_member_is_refused(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    sid = shared_with_bob(client, monkeypatch)
    sign_in(client, monkeypatch, ADMIN)
    assert client.post("/api/admin/bans", json={"email": "bob@example.com"}).status_code == 200
    sign_in(client, monkeypatch, BOB)
    assert upload(client, {}, url=f"/api/sites/{sid}/versions").status_code == 403
    assert client.post(f"/api/sites/{sid}/live", json={"version": 1}).status_code == 403
    sign_in(client, monkeypatch, ALICE)  # the owner isn't affected
    assert upload(client, {}, url=f"/api/sites/{sid}/versions").status_code == 200


def test_storage_counts_against_the_owner_and_uploads_against_the_publisher(client, monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_QUOTA_MB", str(4096 / 1024 / 1024))
    sid = shared_with_bob(client, monkeypatch)  # Alice's site uses 2 bytes ("v1")
    assert upload(client, {}, {"index.html": "b" * 3000}, url=f"/api/sites/{sid}/versions").status_code == 200
    assert client.get("/api/me/usage").json()["used_bytes"] == 0  # Bob's own account
    r = upload(client, {}, {"index.html": "c" * 1500}, url=f"/api/sites/{sid}/versions")
    assert r.status_code == 413  # Alice's quota is full, not Bob's
    sign_in(client, monkeypatch, ALICE)
    assert client.get("/api/me/usage").json()["used_bytes"] == 3002
    counts = dict(sqlite3.connect(tmp_path / "t.db").execute("select sub, count from uploads").fetchall())
    assert counts == {"111": 1, "222": 1}


def test_api_token_member(client, monkeypatch):
    sid = shared_with_bob(client, monkeypatch)
    h = bearer(new_token(client, monkeypatch, BOB)["secret"])
    assert upload(client, h, url=f"/api/sites/{sid}/versions").json()["site"]["live"] == 2
    assert client.post(f"/api/sites/{sid}/live", json={"version": 1}, headers=h).status_code == 200
    assert client.get(f"/api/sites/{sid}/versions/2/files", headers=h).status_code == 200
    assert client.delete(f"/api/sites/{sid}/versions/2", headers=h).status_code == 403
    sites = client.get("/api/me/sites", headers=h).json()["sites"]
    assert [(s["id"], s["role"]) for s in sites] == [(sid, "member")]


def test_pending_invite_becomes_active_on_sign_in(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    assert invite(client, sid, "dave@example.com").json()["members"][0]["status"] == "pending"
    sign_in(client, monkeypatch, {"sub": "444", "email": "Dave@Example.com", "name": "Dave"})
    [site] = client.get("/api/me/sites").json()["sites"]
    assert (site["id"], site["role"], site["owner_email"]) == (sid, "member", "alice@example.com")
    assert upload(client, {}, url=f"/api/sites/{sid}/versions").status_code == 200
    sign_in(client, monkeypatch, ALICE)
    assert client.get(f"/api/sites/{sid}/members").json()["members"][0]["status"] == "accepted"


def test_my_sites_has_roles_and_publishers(client, monkeypatch):
    sid = shared_with_bob(client, monkeypatch)
    upload(client, {}, url=f"/api/sites/{sid}/versions")
    bobs = upload(client, {}).json()
    client.cookies.clear()
    assert (
        upload(client, bearer(bobs["edit_token"]), url=f"/api/sites/{bobs['site']['id']}/versions").status_code == 200
    )
    sign_in(client, monkeypatch, BOB)
    listed = client.get("/api/me/sites").json()["sites"]
    assert [(s["id"], s["role"]) for s in listed] == [(bobs["site"]["id"], "owner"), (sid, "member")]
    assert [v["published_by_email"] for v in listed[0]["versions"]] == ["bob@example.com", None]  # 2: edit token
    assert [v["published_by_email"] for v in listed[1]["versions"]] == ["alice@example.com", "bob@example.com"]
    assert listed[1]["url"].endswith(f"/s/{sid}/")
    assert "published_by_email" not in client.get(f"/api/sites/{sid}").json()["site"]["versions"][0]  # public
