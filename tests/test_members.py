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
