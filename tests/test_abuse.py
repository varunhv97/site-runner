import pytest

import _core.abuse as abuse
from test_api import make_zip
from test_auth import ALICE, BASE, BOB, client, share, sign_in  # noqa: F401 (client is a fixture)

ADMIN = {"sub": "999", "email": "admin@example.com", "name": "Admin"}
CAROL = {"sub": "333", "email": "carol@example.com", "name": "Carol"}


@pytest.fixture(autouse=True)
def roomy_rate_limit(monkeypatch):
    """Tests share one client IP; only test_report_rate_limit checks the limit."""
    monkeypatch.setattr(abuse, "REPORTS_PER_HOUR", 1000)


def report(client, url, reason="phishing", **kw):
    headers = kw.pop("headers", {})
    return client.post("/api/reports", json={"url": url, "reason": reason, **kw}, headers=headers)


def as_admin(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    sign_in(client, monkeypatch, ADMIN)


def open_reports(client, status="open"):
    r = client.get(f"/api/admin/reports?status={status}")
    assert r.status_code == 200, r.text
    return r.json()["reports"]


def alice_site(client, monkeypatch, slug=None):
    sign_in(client, monkeypatch, ALICE)
    r = client.post(
        "/api/sites", files={"file": ("s.zip", make_zip({"index.html": "v1"}))}, data={"slug": slug} if slug else {}
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_report_validation(client, monkeypatch):
    sid = alice_site(client, monkeypatch, slug="trail-map")["site"]["id"]
    client.cookies.clear()  # anyone may report, signed in or not
    assert report(client, f"{BASE}/s/{sid}/").status_code == 200
    assert report(client, f"{BASE}/s/trail-map@1/page.html?x=1").status_code == 200  # link name, pinned, path
    assert report(client, "testserver/s/trail-map/").status_code == 200  # pasted without a scheme

    for url in ("", "not a link", "https://evil.example/s/trail-map/", f"{BASE}/app", f"{BASE}/s/", "ftp://x/s/a/"):
        assert report(client, url).status_code == 400, url
    assert report(client, f"https://user@{BASE[7:]}/s/{sid}/").status_code == 400
    assert report(client, f"{BASE}/s/nosuchsite/").status_code == 404
    assert report(client, f"{BASE}/s/{sid}/", reason="ugly").status_code == 400
    for reason in abuse.REASONS:
        assert report(client, f"{BASE}/s/{sid}/", reason=reason, details="why").status_code == 200, reason
    assert report(client, f"{BASE}/s/{sid}/", details="x" * 2001).status_code == 400
    assert report(client, f"{BASE}/s/{sid}/" + "a" * 500).status_code == 400
    assert report(client, f"{BASE}/s/{sid}/", contact="not-an-email").status_code == 400
    assert report(client, f"{BASE}/s/{sid}/", details="x" * 2000, contact="me@example.com").status_code == 200
    # browser posts must come from the app itself
    assert report(client, f"{BASE}/s/{sid}/", headers={"Origin": "null"}).status_code == 403


def test_user_content_urls(client, monkeypatch):
    sid = alice_site(client, monkeypatch, slug="trail-map")["site"]["id"]
    monkeypatch.delenv("USERCONTENT_BASE_DOMAIN", raising=False)
    assert report(client, f"https://{sid}.sites.spinsite.app/").status_code == 400
    assert report(client, f"http://{sid}.usercontent.localhost:8001/").status_code == 200  # the local default
    monkeypatch.setenv("USERCONTENT_BASE_DOMAIN", "sites.spinsite.app")
    assert report(client, f"https://{sid}.sites.spinsite.app/a/b.html").status_code == 200
    assert report(client, "https://trail-map.sites.spinsite.app/").status_code == 200
    assert report(client, "https://nope.sites.spinsite.app/").status_code == 404
    assert report(client, f"https://x.{sid}.sites.spinsite.app/").status_code == 400
    assert report(client, "https://sites.spinsite.app/").status_code == 400


def test_report_rate_limit(client, monkeypatch):
    monkeypatch.setattr(abuse, "REPORTS_PER_HOUR", 3)
    sid = alice_site(client, monkeypatch)["site"]["id"]
    url = f"{BASE}/s/{sid}/"
    one = {"X-Forwarded-For": "1.2.3.4, 10.0.0.1"}
    assert report(client, f"{BASE}/s/nosuchsite/", headers=one).status_code == 404  # lookups count too
    assert report(client, url, reason="bad", headers=one).status_code == 400  # format errors don't
    assert [report(client, url, headers=one).status_code for _ in range(3)] == [200, 200, 429]
    assert report(client, url, headers={"X-Forwarded-For": "5.6.7.8"}).status_code == 200  # per IP


def test_reports_are_stored_and_admin_only(client, monkeypatch):
    sid = alice_site(client, monkeypatch, slug="trail-map")["site"]["id"]
    sign_in(client, monkeypatch, BOB)
    report(client, f"{BASE}/s/trail-map/", reason="scam", details="asks for card numbers", contact="bob@example.com")
    client.cookies.clear()
    report(client, f"{BASE}/s/{sid}/", reason="spam")

    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    assert client.get("/api/admin/reports").status_code == 403  # anonymous
    sign_in(client, monkeypatch, BOB)
    assert client.get("/api/admin/reports").status_code == 403
    assert client.post("/api/admin/reports/1", json={"status": "dismissed"}).status_code == 403
    assert client.post("/api/admin/bans", json={"email": "alice@example.com"}).status_code == 403
    assert client.delete("/api/admin/bans/alice@example.com").status_code == 403

    sign_in(client, monkeypatch, ADMIN)
    rows = open_reports(client)
    assert [r["reason"] for r in rows] == ["spam", "scam"]  # newest first
    scam = rows[1]
    assert scam["site_id"] == sid and scam["slug"] == "trail-map" and scam["owner_email"] == "alice@example.com"
    assert scam["details"] == "asks for card numbers" and scam["contact"] == "bob@example.com"
    assert scam["site_reports"] == 2 and scam["status"] == "open" and scam["site_url"] == f"{BASE}/s/trail-map/"
    assert scam["site_unpublished"] is False and scam["owner_banned"] is False

    r = client.post(f"/api/admin/reports/{scam['id']}", json={"status": "dismissed"})
    assert r.status_code == 200 and r.json()["report"]["status"] == "dismissed"
    assert client.post(f"/api/admin/reports/{scam['id']}", json={"status": "bogus"}).status_code == 400
    assert client.post("/api/admin/reports/12345", json={"status": "actioned"}).status_code == 404
    assert [r["reason"] for r in open_reports(client)] == ["spam"]
    assert [r["reason"] for r in open_reports(client, "dismissed")] == ["scam"]
    assert len(open_reports(client, "all")) == 2
    assert client.get("/api/admin/reports?status=nope").status_code == 400


def test_admin_unpublish_actions_reports(client, monkeypatch):
    sid = alice_site(client, monkeypatch)["site"]["id"]
    other = alice_site(client, monkeypatch)["site"]["id"]
    report(client, f"{BASE}/s/{sid}/")
    report(client, f"{BASE}/s/{other}/")
    as_admin(client, monkeypatch)
    assert client.delete(f"/api/sites/{sid}").status_code == 200
    assert [r["site_id"] for r in open_reports(client)] == [other]
    actioned = open_reports(client, "actioned")
    assert [r["site_id"] for r in actioned] == [sid] and actioned[0]["site_unpublished"] is True
    assert report(client, f"{BASE}/s/{sid}/").status_code == 404  # gone sites can't be reported


def write_attempts(client, sid, headers=None):
    """Status of every share write path on site sid."""
    h = headers or {}
    v = {"file": ("s.zip", make_zip({"index.html": "v2"}))}
    return {
        "version": client.post(f"/api/sites/{sid}/versions", files=v, headers=h).status_code,
        "live": client.post(f"/api/sites/{sid}/live", json={"version": 1}, headers=h).status_code,
        "entry": client.post(f"/api/sites/{sid}/versions/1/entry", json={"entry": "index.html"}, headers=h).status_code,
        "slug": client.post(f"/api/sites/{sid}/slug", json={"slug": "new-name"}, headers=h).status_code,
        "token": client.post(f"/api/sites/{sid}/token", headers=h).status_code,
    }


def test_ban_blocks_signed_in_writes_and_unpublishes(client, monkeypatch):
    first = alice_site(client, monkeypatch)
    second = alice_site(client, monkeypatch)
    sid = first["site"]["id"]
    report(client, f"{BASE}/s/{sid}/")

    as_admin(client, monkeypatch)
    assert client.post("/api/admin/bans", json={"email": "bad"}).status_code == 400
    assert client.post("/api/admin/bans", json={"email": "admin@example.com"}).status_code == 400
    r = client.post("/api/admin/bans", json={"email": "Alice@Example.com", "reason": "phishing kit"})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["sites_unpublished"]) == sorted([sid, second["site"]["id"]])
    assert r.json()["ban"]["email"] == "alice@example.com"
    assert client.get(f"/s/{sid}/").status_code == 404
    assert open_reports(client) == [] and open_reports(client, "actioned")[0]["owner_banned"] is True
    assert [b["email"] for b in client.get("/api/admin/bans").json()["bans"]] == ["alice@example.com"]

    # sign-in still works, so Alice sees why she can't share
    sign_in(client, monkeypatch, ALICE)
    r = share(client)
    assert r.status_code == 403 and r.json()["error"].startswith("This account can't share sites.")
    assert client.get("/api/me/sites").json()["sites"] == []

    # claiming a site shared without an account (sign-in off, token only)
    client.cookies.clear()
    monkeypatch.delenv("GOOGLE_CLIENT_ID")
    anon = share(client).json()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    sign_in(client, monkeypatch, ALICE)
    r = client.post(f"/api/sites/{anon['site']['id']}/claim", headers={"Authorization": f"Bearer {anon['edit_token']}"})
    assert r.status_code == 403

    # writing to someone else's site with its edit token
    sign_in(client, monkeypatch, BOB)
    bob = share(client).json()
    sign_in(client, monkeypatch, ALICE)
    attempts = write_attempts(client, bob["site"]["id"], {"Authorization": f"Bearer {bob['edit_token']}"})
    assert set(attempts.values()) == {403}, attempts


def test_ban_blocks_edit_token_writes_to_the_owners_sites(client, monkeypatch):
    import asyncio

    from sqlalchemy import update

    import api.index as server
    from _core.db import sites as sites_t

    body = alice_site(client, monkeypatch)
    sid, token = body["site"]["id"], {"Authorization": f"Bearer {body['edit_token']}"}
    as_admin(client, monkeypatch)
    client.post("/api/admin/bans", json={"email": "alice@example.com"})

    async def republish():  # a banned owner's site that is still up, e.g. put back by hand
        async with server.sites.engine.begin() as conn:
            await conn.execute(update(sites_t).where(sites_t.c.id == sid).values(deleted_at=None))

    asyncio.run(republish())
    client.cookies.clear()
    attempts = write_attempts(client, sid, token)
    assert set(attempts.values()) == {403}, attempts
    assert client.delete(f"/api/sites/{sid}", headers=token).status_code == 200  # taking it down is still allowed


def test_unban_restores_sharing_but_not_sites(client, monkeypatch):
    sid = alice_site(client, monkeypatch)["site"]["id"]
    as_admin(client, monkeypatch)
    client.post("/api/admin/bans", json={"email": "alice@example.com", "reason": "spam"})
    assert client.delete("/api/admin/bans/alice@example.com").status_code == 200
    assert client.delete("/api/admin/bans/alice@example.com").status_code == 404
    assert client.get("/api/admin/bans").json()["bans"] == []

    sign_in(client, monkeypatch, ALICE)
    assert client.get(f"/s/{sid}/").status_code == 404  # unpublished sites stay down
    new = share(client)
    assert new.status_code == 200
    assert set(write_attempts(client, new.json()["site"]["id"]).values()) == {200}


@pytest.fixture
def sent(monkeypatch):
    """Capture report emails instead of calling Resend."""
    import _core.mailer as mailer

    out = []

    async def fake_send(to, subject, text, reply_to=None):
        out.append({"to": to, "subject": subject, "text": text, "reply_to": reply_to})
        return True

    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("REPORT_EMAIL", "abuse@spinsite.app")
    monkeypatch.setattr(mailer, "send", fake_send)
    return out


def test_a_report_emails_the_team(client, monkeypatch, sent):
    sign_in(client, monkeypatch, ALICE)
    r = client.post(
        "/api/sites",
        files={"file": ("s.zip", make_zip({"index.html": "x"}))},
        data={"name": "x", "slug": "bucket-game"},
    )
    client.cookies.clear()
    r = report(client, f"{BASE}/s/bucket-game/", "scam", details="Asks for card numbers", contact="me@example.com")
    assert r.status_code == 200, r.text
    assert len(sent) == 1
    mail = sent[0]
    assert mail["to"] == "abuse@spinsite.app" and mail["reply_to"] == "me@example.com"
    assert "Scam" in mail["subject"] and "bucket-game" in mail["subject"]
    assert "alice@example.com" in mail["text"]  # the owner, so the admin can act straight away
    assert "Asks for card numbers" in mail["text"] and f"{BASE}/admin" in mail["text"]


def test_no_email_without_config_and_reports_survive_mail_failures(client, monkeypatch, sent):
    import _core.mailer as mailer

    sign_in(client, monkeypatch, ALICE)
    sid = share(client).json()["site"]["id"]
    client.cookies.clear()
    monkeypatch.delenv("REPORT_EMAIL")
    assert report(client, f"{BASE}/s/{sid}/").status_code == 200
    assert sent == []  # no destination: nothing sent, report still stored

    monkeypatch.setenv("REPORT_EMAIL", "abuse@spinsite.app")

    async def failing(*a, **kw):
        return False

    monkeypatch.setattr(mailer, "send", failing)
    assert report(client, f"{BASE}/s/{sid}/").status_code == 200  # a mail outage never loses a report
    as_admin(client, monkeypatch)
    assert len(open_reports(client)) == 2


def test_mailer_calls_resend(monkeypatch):
    import asyncio

    import httpx

    import _core.mailer as mailer

    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"id": "e1"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert asyncio.run(mailer.send("abuse@spinsite.app", "hi", "body")) is False  # no key: off
    monkeypatch.setenv("RESEND_API_KEY", "re_123")
    assert asyncio.run(mailer.send("abuse@spinsite.app", "hi", "body", reply_to="me@example.com")) is True
    assert seen["auth"] == "Bearer re_123"
    assert b'"reply_to":"me@example.com"' in seen["body"].replace(b" ", b"")
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(422)), **kw)
    )
    assert asyncio.run(mailer.send("abuse@spinsite.app", "hi", "body")) is False  # refused: False, no exception


def test_something_else_needs_details(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    sid = share(client).json()["site"]["id"]
    client.cookies.clear()
    r = report(client, f"{BASE}/s/{sid}/", "other")
    assert r.status_code == 400 and "details" in r.json()["error"]
    assert report(client, f"{BASE}/s/{sid}/", "other", details="Impersonates my business").status_code == 200
