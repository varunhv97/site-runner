from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import api.index as server
from _core import auth
from _core.sites import Sites
from _core.db import make_engine
from _core.storage import LocalStorage
from test_api import make_zip

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
