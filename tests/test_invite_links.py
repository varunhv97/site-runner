import asyncio
import hashlib
import sqlite3

import pytest

import api.index as server
from _core import invite_links, members
from _core.sites import SiteError
from test_api_tokens import bearer, new_token, upload
from test_auth import ALICE, BOB, client, sign_in  # noqa: F401  (client is a fixture)
from test_members import ADMIN, CAROL, accept, alices_site, invite

GONE = {"error": invite_links.INVALID}


@pytest.fixture(autouse=True)
def generous_tries(monkeypatch):
    """Every test client shares one IP; the rate limit test sets its own limit."""
    monkeypatch.setattr(invite_links, "TRIES_PER_HOUR", 1000)


def create(client, sid, **kw):
    return client.post(f"/api/sites/{sid}/invite-links", **kw)


def new_link(client, sid) -> str:
    r = create(client, sid)
    assert r.status_code == 200, r.text
    return r.json()["url"].split("#", 1)[1]


def lookup(client, code, **kw):
    return client.post("/api/invite-links/lookup", json={"code": code}, **kw)


def redeem(client, code, **kw):
    return client.post("/api/invite-links/redeem", json={"code": code}, **kw)


def links(client, sid):
    r = client.get(f"/api/sites/{sid}/invite-links")
    assert r.status_code == 200, r.text
    return r.json()


def db(tmp_path):
    return sqlite3.connect(tmp_path / "t.db")


def test_create_list_and_revoke(client, monkeypatch, tmp_path):
    sid = alices_site(client, monkeypatch)
    r = create(client, sid)
    assert r.status_code == 200, r.text
    body = r.json()
    code = body["url"].split("#", 1)[1]
    assert body["url"] == f"http://testserver/join#{code}"
    assert code.startswith("srj_") and len(code) >= 4 + 43  # 32 random bytes
    link = body["link"]
    assert set(link) == {"id", "created_at", "expires_at"} and link["expires_at"] == link["created_at"] + 3600

    listed = links(client, sid)
    assert listed == {"links": [link], "used": []}
    assert code not in r.text.replace(body["url"], "") and code not in str(listed)
    [(stored,)] = db(tmp_path).execute("select token_hash from invite_links").fetchall()
    assert stored == hashlib.sha256(code.encode()).hexdigest() and code not in stored

    assert client.delete(f"/api/sites/{sid}/invite-links/{link['id']}").status_code == 200
    assert links(client, sid)["links"] == []
    assert client.delete(f"/api/sites/{sid}/invite-links/{link['id']}").status_code == 404
    sign_in(client, monkeypatch, BOB)
    assert lookup(client, code).status_code == 404


def test_only_the_owner_manages_links(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    link_id = create(client, sid).json()["link"]["id"]
    assert create(client, sid, headers={"Origin": "https://evil.example"}).status_code == 403
    assert (
        client.delete(f"/api/sites/{sid}/invite-links/{link_id}", headers={"Origin": "https://x.example"}).status_code
        == 403
    )
    invite(client, sid, "bob@example.com")
    sign_in(client, monkeypatch, BOB)
    assert accept(client, sid).status_code == 200  # a member, not the owner
    for who in (BOB, CAROL):
        sign_in(client, monkeypatch, who)
        assert create(client, sid).status_code == 403
        assert client.get(f"/api/sites/{sid}/invite-links").status_code == 403
        assert client.delete(f"/api/sites/{sid}/invite-links/{link_id}").status_code == 403
    client.cookies.clear()
    assert create(client, sid).status_code == 401
    assert client.get(f"/api/sites/{sid}/invite-links").status_code == 401
    sign_in(client, monkeypatch, ALICE)
    assert len(links(client, sid)["links"]) == 1  # still there


def test_api_token_owner_makes_links_edit_token_doesnt(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    shared = upload(client, {}).json()
    sid = shared["site"]["id"]
    h = bearer(new_token(client, monkeypatch, ALICE)["secret"])
    assert create(client, sid, headers=h).status_code == 200
    assert len(client.get(f"/api/sites/{sid}/invite-links", headers=h).json()["links"]) == 1
    assert create(client, sid, headers=bearer(shared["edit_token"])).status_code == 401


def test_redeem_joins_once(client, monkeypatch, tmp_path):
    sid = alices_site(client, monkeypatch)
    code = new_link(client, sid)
    sign_in(client, monkeypatch, BOB)
    r = lookup(client, code)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"site_name", "owner_email", "expires_at"} and r.json()["owner_email"] == ALICE["email"]
    assert client.get("/api/me/sites").json()["sites"] == []  # looking grants nothing

    assert redeem(client, code, headers={"Origin": "https://evil.example"}).status_code == 403
    r = redeem(client, code)
    assert r.status_code == 200, r.text
    assert (r.json()["site"]["id"], r.json()["site"]["role"]) == (sid, "member")
    assert r.json()["site"]["url"].endswith(f"/s/{sid}/")
    assert [(s["id"], s["role"]) for s in client.get("/api/me/sites").json()["sites"]] == [(sid, "member")]
    assert upload(client, {}, url=f"/api/sites/{sid}/versions").json()["site"]["live"] == 2  # publish
    assert client.get(f"/api/sites/{sid}/versions/2/files").status_code == 200  # pull

    assert redeem(client, code).json() == GONE  # used up, even for the account that used it
    sign_in(client, monkeypatch, CAROL)
    assert lookup(client, code).json() == GONE
    r = redeem(client, code)
    assert r.status_code == 404 and r.json() == GONE
    assert client.get("/api/me/sites").json()["sites"] == []

    row = db(tmp_path).execute("select email, sub, accepted_at, invited_by from site_members").fetchall()
    assert [(e, s, by) for e, s, a, by in row] == [("bob@example.com", "222", "111")] and row[0][2]
    sign_in(client, monkeypatch, ALICE)
    listed = links(client, sid)
    assert listed["links"] == []
    assert [(u["used_by_email"]) for u in listed["used"]] == ["bob@example.com"]
    assert [(m["email"], m["status"]) for m in client.get(f"/api/sites/{sid}/members").json()["members"]] == [
        ("bob@example.com", "accepted")
    ]


def test_failures_look_the_same(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    revoked, expired, used = new_link(client, sid), new_link(client, sid), new_link(client, sid)
    link_id = links(client, sid)["links"][0]["id"]
    assert client.delete(f"/api/sites/{sid}/invite-links/{link_id}").status_code == 200
    sign_in(client, monkeypatch, BOB)
    assert redeem(client, used).status_code == 200
    real_now = invite_links._now()
    monkeypatch.setattr(invite_links, "_now", lambda: real_now + 3600)  # exactly an hour later: expired
    sign_in(client, monkeypatch, CAROL)
    for code in (revoked, expired, used, "srj_" + "A" * 43, "nonsense", "", "srj_" + "x" * 500):
        for call in (lookup, redeem):
            r = call(client, code)
            assert (r.status_code, r.json()) == (404, GONE), (code, r.text)


def test_expires_after_an_hour(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    code = new_link(client, sid)
    real_now = invite_links._now()
    monkeypatch.setattr(invite_links, "_now", lambda: real_now + 3599)
    sign_in(client, monkeypatch, BOB)
    assert lookup(client, code).status_code == 200
    monkeypatch.setattr(invite_links, "_now", lambda: real_now + 3601)
    assert redeem(client, code).json() == GONE
    sign_in(client, monkeypatch, ALICE)
    assert links(client, sid)["links"] == []  # not outstanding any more


def test_lookup_and_redeem_need_sign_in(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    code = new_link(client, sid)
    client.cookies.clear()
    assert lookup(client, code).status_code == 401
    assert redeem(client, code).status_code == 401
    sign_in(client, monkeypatch, BOB)
    assert lookup(client, code).status_code == 200  # still unused


def test_owner_cant_redeem_and_the_link_stays(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    code = new_link(client, sid)
    r = redeem(client, code)
    assert r.status_code == 400 and "your own site" in r.json()["error"]
    assert len(links(client, sid)["links"]) == 1
    sign_in(client, monkeypatch, BOB)
    assert redeem(client, code).status_code == 200


def test_existing_member_doesnt_use_the_link_up(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    invite(client, sid, "bob@example.com")
    code = new_link(client, sid)
    sign_in(client, monkeypatch, BOB)
    assert accept(client, sid).status_code == 200
    r = redeem(client, code)
    assert r.status_code == 409 and "already a member" in r.json()["error"]
    sign_in(client, monkeypatch, CAROL)
    assert redeem(client, code).status_code == 200


def test_pending_email_invite_is_converted(client, monkeypatch, tmp_path):
    sid = alices_site(client, monkeypatch)
    invite(client, sid, "bob@example.com")
    code = new_link(client, sid)
    sign_in(client, monkeypatch, BOB)
    assert redeem(client, code).status_code == 200
    assert client.get("/api/me/invites").json()["invites"] == []
    rows = db(tmp_path).execute("select email, sub, accepted_at is not null from site_members").fetchall()
    assert rows == [("bob@example.com", "222", 1)]


def test_banned_account_or_owner_is_refused(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    sid = alices_site(client, monkeypatch)
    code = new_link(client, sid)
    sign_in(client, monkeypatch, ADMIN)
    assert client.post("/api/admin/bans", json={"email": "bob@example.com"}).status_code == 200
    sign_in(client, monkeypatch, BOB)
    assert redeem(client, code).status_code == 403
    sign_in(client, monkeypatch, CAROL)
    assert redeem(client, code).status_code == 200  # wasn't used up

    sign_in(client, monkeypatch, ALICE)
    other = upload(client, {}).json()["site"]["id"]
    code = new_link(client, other)
    sign_in(client, monkeypatch, ADMIN)
    assert client.post("/api/admin/bans", json={"email": "alice@example.com"}).status_code == 200
    sign_in(client, monkeypatch, CAROL)
    assert redeem(client, code).status_code == 404  # the ban unpublished Alice's sites
    sign_in(client, monkeypatch, ALICE)
    assert create(client, sid).status_code == 403


def test_outstanding_links_count_toward_the_member_cap(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    for i in range(members.MAX_MEMBERS - 2):
        assert invite(client, sid, f"user{i}@example.com").status_code == 200
    code = new_link(client, sid)
    revoked = create(client, sid).json()["link"]["id"]
    assert client.get(f"/api/sites/{sid}/members").json()["invite_links"] == 2
    r = create(client, sid)
    assert r.status_code == 409 and "unused invite links" in r.json()["error"]
    assert invite(client, sid, "one-more@example.com").status_code == 409
    assert client.delete(f"/api/sites/{sid}/invite-links/{revoked}").status_code == 200
    assert invite(client, sid, "one-more@example.com").status_code == 200  # the revoked link's seat
    sign_in(client, monkeypatch, BOB)
    assert redeem(client, code).status_code == 200  # the link's own seat becomes Bob's
    sign_in(client, monkeypatch, ALICE)
    assert len(client.get(f"/api/sites/{sid}/members").json()["members"]) == members.MAX_MEMBERS
    assert create(client, sid).status_code == 409


def test_expired_links_free_their_seat(client, monkeypatch):
    monkeypatch.setattr(members, "MAX_MEMBERS", 1)
    monkeypatch.setattr(invite_links, "MAX_MEMBERS", 1)
    sid = alices_site(client, monkeypatch)
    new_link(client, sid)
    assert create(client, sid).status_code == 409
    real_now = invite_links._now()
    monkeypatch.setattr(invite_links, "_now", lambda: real_now + 3600)
    assert create(client, sid).status_code == 200


def test_unpublished_site(client, monkeypatch):
    sid = alices_site(client, monkeypatch)
    code = new_link(client, sid)
    assert client.delete(f"/api/sites/{sid}").status_code == 200
    assert create(client, sid).status_code == 404
    sign_in(client, monkeypatch, BOB)
    assert lookup(client, code).json() == GONE
    assert redeem(client, code).json() == GONE


def test_tries_are_rate_limited_per_account_and_ip(client, monkeypatch):
    monkeypatch.setattr(invite_links, "TRIES_PER_HOUR", 3)
    sid = alices_site(client, monkeypatch)
    code = new_link(client, sid)
    sign_in(client, monkeypatch, BOB)
    for _ in range(3):
        assert lookup(client, "srj_guess").status_code == 404
    assert lookup(client, code).status_code == 429  # failed tries count
    assert redeem(client, code).status_code == 429
    sign_in(client, monkeypatch, CAROL)  # another account, same IP
    assert lookup(client, code).status_code == 429
    monkeypatch.setattr(server, "_client_ip", lambda request: "203.0.113.9")
    assert redeem(client, code).status_code == 200


def test_creating_links_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(invite_links, "CREATES_PER_HOUR", 2)
    sid = alices_site(client, monkeypatch)
    assert create(client, sid).status_code == 200
    assert create(client, sid).status_code == 200
    assert create(client, sid).status_code == 429


def test_only_one_of_two_redeems_wins(client, monkeypatch, tmp_path):
    sid = alices_site(client, monkeypatch)
    code = new_link(client, sid)
    sign_in(client, monkeypatch, BOB)  # records Bob and Carol in users
    sign_in(client, monkeypatch, CAROL)

    async def race():
        return await asyncio.gather(
            invite_links.redeem(server.sites, code, BOB, "198.51.100.1"),
            invite_links.redeem(server.sites, code, CAROL, "198.51.100.2"),
            return_exceptions=True,
        )

    results = asyncio.run(race())
    wins = [r for r in results if isinstance(r, dict)]
    assert len(wins) == 1, results
    [lost] = [r for r in results if r is not wins[0]]
    assert isinstance(lost, SiteError) and lost.status == 404, lost
    assert db(tmp_path).execute("select count(*) from site_members").fetchone() == (1,)
    assert db(tmp_path).execute("select count(*) from invite_links where used_at is not null").fetchone() == (1,)


def test_the_used_guard_holds_on_a_stale_read(client, monkeypatch, tmp_path):
    """Even when the checks before it read the link as unused, the guarded UPDATE lets only one redeem through."""
    sid = alices_site(client, monkeypatch)
    code = new_link(client, sid)
    sign_in(client, monkeypatch, BOB)
    sign_in(client, monkeypatch, CAROL)

    async def read_unused():
        async with server.sites.engine.connect() as conn:
            return await invite_links._find(conn, code, invite_links._now())

    stale = asyncio.run(read_unused())
    asyncio.run(invite_links.redeem(server.sites, code, BOB, "198.51.100.1"))

    async def stale_find(conn, c, now):
        return stale

    monkeypatch.setattr(invite_links, "_find", stale_find)
    with pytest.raises(SiteError) as e:
        asyncio.run(invite_links.redeem(server.sites, code, CAROL, "198.51.100.2"))
    assert e.value.status == 404 and str(e.value) == invite_links.INVALID
    assert db(tmp_path).execute("select email from site_members").fetchall() == [("bob@example.com",)]
