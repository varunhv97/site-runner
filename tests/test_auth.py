from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import api.index as server
from _core import auth
from _core.sites import Sites
from _core.db import make_engine
from _core.storage import LocalStorage
from test_api import make_zip, page

BASE = "http://testserver"
ALICE = {"sub": "111", "email": "alice@example.com", "name": "Alice"}
BOB = {"sub": "222", "email": "bob@example.com", "name": "Bob"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setattr(server, "sites", Sites(LocalStorage(str(tmp_path)), make_engine(f"sqlite:///{tmp_path}/t.db")))
    return TestClient(server.app)


def sign_in(client, monkeypatch, user):
    async def fake_fetch(code, redirect_uri):
        assert code == "the-code" and redirect_uri == f"{BASE}/api/auth/callback"
        return user

    monkeypatch.setattr(auth, "fetch_google_user", fake_fetch)
    r = client.get("/api/auth/login?next=/app", follow_redirects=False)
    assert r.status_code == 302
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = client.get(f"/api/auth/callback?code=the-code&state={state}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/app"


def share(client, **kw):
    return client.post("/api/sites", files={"file": ("s.zip", make_zip({"index.html": "v1"}))}, **kw)


def test_signing_and_tamper(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "s")
    value = auth.session_value(ALICE)
    assert auth.verify(value)["sub"] == "111"
    body, mac = value.rsplit(".", 1)
    assert auth.verify(body + "x." + mac) is None
    monkeypatch.setenv("SESSION_SECRET", "other")
    assert auth.verify(value) is None


def test_login_redirects_to_google(client):
    r = client.get("/api/auth/login", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.headers["location"].startswith(auth.GOOGLE_AUTH_URL)
    assert q["client_id"] == ["cid"] and q["scope"] == ["openid email profile"]
    assert "httponly" in r.headers["set-cookie"].lower()


def test_callback_rejects_bad_state(client, monkeypatch):
    client.get("/api/auth/login", follow_redirects=False)
    r = client.get("/api/auth/callback?code=x&state=wrong", follow_redirects=False)
    assert r.headers["location"] == "/app?signin=failed"
    assert client.get("/api/auth/me").json()["user"] is None


def test_share_requires_sign_in(client, monkeypatch):
    assert share(client).status_code == 401
    sign_in(client, monkeypatch, ALICE)
    assert client.get("/api/auth/me").json()["user"]["email"] == "alice@example.com"
    r = share(client)
    assert r.status_code == 200
    sid = r.json()["site"]["id"]
    assert [s["id"] for s in client.get("/api/me/sites").json()["sites"]] == [sid]


def test_owner_publishes_without_token_others_cannot(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    body = share(client).json()
    sid, token = body["site"]["id"], body["edit_token"]
    v2 = {"file": ("s.zip", make_zip({"index.html": "v2"}))}
    assert client.post(f"/api/sites/{sid}/versions", files=v2).status_code == 200

    sign_in(client, monkeypatch, BOB)
    assert client.post(f"/api/sites/{sid}/versions", files=v2).status_code == 403
    assert client.post(f"/api/sites/{sid}/live", json={"version": 1}).status_code == 403
    # the edit token still works, for API and MCP clients
    r = client.post(f"/api/sites/{sid}/live", json={"version": 1}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_writes_from_shared_sites_are_refused(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    assert share(client, headers={"Origin": "null"}).status_code == 403
    assert share(client, headers={"Origin": "https://evil.example"}).status_code == 403
    assert share(client, headers={"Origin": BASE}).status_code == 200


def test_claim_with_token(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    body = share(client).json()
    sid, token = body["site"]["id"], body["edit_token"]
    sign_in(client, monkeypatch, BOB)
    r = client.post(f"/api/sites/{sid}/claim", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 409  # already Alice's


def test_unpublish_and_admin_takedown(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    sign_in(client, monkeypatch, ALICE)
    a = share(client).json()["site"]["id"]
    b = share(client).json()["site"]["id"]
    assert client.delete(f"/api/sites/{a}").status_code == 200
    assert client.get(f"/s/{a}/").status_code == 404
    assert [s["id"] for s in client.get("/api/me/sites").json()["sites"]] == [b]

    sign_in(client, monkeypatch, BOB)
    assert client.delete(f"/api/sites/{b}").status_code == 403
    sign_in(client, monkeypatch, {"sub": "999", "email": "admin@example.com", "name": "Admin"})
    assert client.delete(f"/api/sites/{b}").status_code == 200
    assert client.get(f"/s/{b}/").status_code == 404


def test_rotate_token(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    body = share(client).json()
    sid, old = body["site"]["id"], body["edit_token"]
    new = client.post(f"/api/sites/{sid}/token").json()["edit_token"]
    v = {"file": ("s.zip", make_zip({"index.html": "v2"}))}
    client.cookies.clear()
    assert (
        client.post(f"/api/sites/{sid}/versions", files=v, headers={"Authorization": f"Bearer {old}"}).status_code
        == 403
    )
    assert (
        client.post(f"/api/sites/{sid}/versions", files=v, headers={"Authorization": f"Bearer {new}"}).status_code
        == 200
    )


def test_limits(client, monkeypatch):
    import _core.sites as core

    monkeypatch.setattr(core, "MAX_SITES_PER_USER", 2)
    monkeypatch.setattr(core, "MAX_UPLOADS_PER_DAY", 3)
    monkeypatch.setattr(core, "MAX_VERSIONS_PER_SITE", 2)
    sign_in(client, monkeypatch, ALICE)
    sid = share(client).json()["site"]["id"]
    v = {"file": ("s.zip", make_zip({"index.html": "v2"}))}
    assert client.post(f"/api/sites/{sid}/versions", files=v).status_code == 200
    assert client.post(f"/api/sites/{sid}/versions", files=v).status_code == 429  # versions cap
    assert share(client).status_code == 200  # second site, third upload
    assert share(client).status_code == 429  # sites cap


def share_named(client, slug, text="v1"):
    return client.post("/api/sites", files={"file": ("s.zip", make_zip({"index.html": text}))}, data={"slug": slug})


def test_link_names_are_unique_across_users(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    r = share_named(client, "learn-sql")
    assert r.status_code == 200 and r.json()["url"] == f"{BASE}/s/learn-sql/"
    sid = r.json()["site"]["id"]
    assert page(client, "/s/learn-sql/") == "v1"
    assert page(client, f"/s/{sid}/") == "v1"  # the random id keeps working
    assert client.get("/api/slugs/learn-sql").json() == {"available": False, "reason": "That name is taken."}

    sign_in(client, monkeypatch, BOB)
    assert share_named(client, "learn-sql").status_code == 409
    assert share_named(client, "Learn-SQL").status_code == 409  # names are lower-cased
    for bad, status in [("api", 400), ("ab", 400), ("-x-", 400), ("a--b", 400), ("has space", 400)]:
        assert share_named(client, bad).status_code == status, bad
    assert client.get("/api/slugs/api").json()["available"] is False


def test_rename_keeps_old_links_and_never_frees_names(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    sid = share_named(client, "trail-map").json()["site"]["id"]
    client.post(f"/api/sites/{sid}/versions", files={"file": ("s.zip", make_zip({"index.html": "v2"}))})
    r = client.post(f"/api/sites/{sid}/slug", json={"slug": "ridge-loop"})
    assert r.status_code == 200 and r.json()["url"] == f"{BASE}/s/ridge-loop/"
    assert page(client, "/s/ridge-loop/") == "v2"
    old = client.get("/s/trail-map@1/index.html", follow_redirects=False)
    assert old.status_code == 308 and old.headers["location"] == f"https://{sid}.sites.spinsite.app/@1/index.html"
    assert page(client, "/s/trail-map@1/") == "v1"  # following the redirect, pinned version kept

    client.delete(f"/api/sites/{sid}")
    sign_in(client, monkeypatch, BOB)
    assert share_named(client, "ridge-loop").status_code == 409  # unpublished names stay reserved
    assert share_named(client, "trail-map").status_code == 409


def test_link_names_cannot_shadow_ids(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    sid = share(client).json()["site"]["id"]
    sign_in(client, monkeypatch, BOB)
    assert share_named(client, sid).status_code == 409  # someone's random id is not a free name
    assert page(client, f"/s/{sid}/") == "v1"


def login_then_callback(client, monkeypatch, next_path):
    """Where the callback sends the browser after signing in with ?next=next_path."""

    async def fake_fetch(code, redirect_uri):
        return ALICE

    monkeypatch.setattr(auth, "fetch_google_user", fake_fetch)
    r = client.get("/api/auth/login", params={"next": next_path}, follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = client.get(f"/api/auth/callback?code=c&state={state}", follow_redirects=False)
    return r.headers["location"]


def test_next_only_redirects_within_this_site(client, monkeypatch):
    for evil in ("/\\evil.com", "//evil.com", "https://evil.com", "/\t/evil.com", "/\\/evil.com"):
        assert login_then_callback(client, monkeypatch, evil) == "/app", evil
    assert login_then_callback(client, monkeypatch, "/app?x=1") == "/app?x=1"


def test_token_uploads_count_against_the_owners_daily_limit(client, monkeypatch):
    import _core.sites as core

    monkeypatch.setattr(core, "MAX_UPLOADS_PER_DAY", 3)
    sign_in(client, monkeypatch, ALICE)
    body = share(client).json()  # upload 1, charged to Alice
    client.cookies.clear()  # from here on only the edit token, no session
    token = {"Authorization": f"Bearer {body['edit_token']}"}
    v = {"file": ("s.zip", make_zip({"index.html": "v2"}))}
    for _ in range(2):  # uploads 2 and 3
        assert client.post(f"/api/sites/{body['site']['id']}/versions", files=v, headers=token).status_code == 200
    assert client.post(f"/api/sites/{body['site']['id']}/versions", files=v, headers=token).status_code == 429


def test_production_requires_public_base_url(client, monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    r = client.get("/api/auth/login", headers={"X-Forwarded-Host": "evil.example"}, follow_redirects=False)
    assert r.status_code == 503  # never trusts the Host / X-Forwarded-Host headers there
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://spinsite.app")
    r = client.get("/api/auth/login", headers={"X-Forwarded-Host": "evil.example"}, follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["redirect_uri"] == ["https://spinsite.app/api/auth/callback"]


def test_cookies_use_the_host_prefix_over_https(client, monkeypatch):
    """Shared sites live on subdomains of the app's domain, so the app's cookies must be __Host- cookies:
    a subdomain can't set those (no Domain attribute allowed), which stops it planting a session."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://spinsite.app")
    r = client.get("/api/auth/login", follow_redirects=False)
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-sr_oauth_state=") and "Path=/;" in cookie and "Domain" not in cookie
    assert "secure" in cookie.lower()

    async def fake_fetch(code, redirect_uri):
        return ALICE

    monkeypatch.setattr(auth, "fetch_google_user", fake_fetch)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    signed = r.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    r = client.get(
        f"/api/auth/callback?code=c&state={state}",
        headers={"Cookie": f"__Host-sr_oauth_state={signed}"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and r.headers["location"] == "/app"
    cookies = r.headers.get_list("set-cookie")
    session = next(c for c in cookies if c.startswith("__Host-sr_session="))
    assert "secure" in session.lower() and "Path=/" in session and "Domain" not in session
    value = session.split(";", 1)[0].split("=", 1)[1]
    me = client.get("/api/auth/me", headers={"Cookie": f"__Host-sr_session={value}"}).json()
    assert me["user"]["email"] == ALICE["email"]
    # the old, unprefixed name is no longer read
    assert client.get("/api/auth/me", headers={"Cookie": f"sr_session={value}"}).json()["user"] is None


def test_plain_cookie_names_over_http(client):
    # local dev over http: __Host- cookies need Secure, which browsers drop on http
    cookie = client.get("/api/auth/login", follow_redirects=False).headers["set-cookie"]
    assert cookie.startswith("sr_oauth_state=") and "Path=/;" in cookie


def test_writes_from_shared_site_subdomains_are_refused(client, monkeypatch):
    """Shared sites are same-site with the app (sites.spinsite.app), so the browser sends the session cookie
    with their requests. The exact-origin check is what refuses them."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://spinsite.app")
    monkeypatch.setattr(server, "_current_user", lambda request: ALICE)
    for origin in ("https://abc.sites.spinsite.app", "https://p-0123456789ab.sites.spinsite.app", "null"):
        assert share(client, headers={"Origin": origin}).status_code == 403, origin
        assert client.post("/api/sites/abc/token", headers={"Origin": origin}).status_code == 403, origin
        assert client.post("/api/auth/logout", headers={"Origin": origin}).status_code == 403, origin
    assert share(client, headers={"Origin": "https://spinsite.app"}).status_code == 200
