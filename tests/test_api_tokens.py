import sqlite3

from _core import api_tokens
from test_api import make_zip
from test_auth import ALICE, BOB, client, sign_in  # noqa: F401  (client is a fixture)

ADMIN = {"sub": "999", "email": "admin@example.com", "name": "Admin"}


def new_token(client, monkeypatch, user, name="laptop"):
    sign_in(client, monkeypatch, user)
    r = client.post("/api/me/tokens", json={"name": name})
    assert r.status_code == 200, r.text
    client.cookies.clear()  # from here on the token is the only credential
    return r.json()


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def upload(client, headers, files=None, url="/api/sites", **data):
    zipped = make_zip(files or {"index.html": "v1"})
    return client.post(url, files={"file": ("s.zip", zipped)}, data=data, headers=headers)


def test_token_acts_as_the_account(client, monkeypatch):
    body = new_token(client, monkeypatch, ALICE)
    secret, h = body["secret"], bearer(body["secret"])
    assert secret.startswith(api_tokens.PREFIX) and body["token"]["hint"] == secret[-4:]
    assert client.get("/api/auth/me", headers=h).json()["user"]["email"] == "alice@example.com"

    r = upload(client, h, name="from the cli")
    assert r.status_code == 200, r.text
    sid = r.json()["site"]["id"]
    assert [s["id"] for s in client.get("/api/me/sites", headers=h).json()["sites"]] == [sid]
    assert upload(client, h, {"index.html": "v2"}, url=f"/api/sites/{sid}/versions").json()["site"]["live"] == 2
    assert client.post(f"/api/sites/{sid}/live", json={"version": 1}, headers=h).json()["site"]["live"] == 1
    assert client.delete(f"/api/sites/{sid}/versions/2", headers=h).status_code == 200
    assert client.get("/api/me/usage", headers=h).json()["used_bytes"] > 0

    # it's the account's: Alice's browser session sees the site too
    sign_in(client, monkeypatch, ALICE)
    assert [s["id"] for s in client.get("/api/me/sites").json()["sites"]] == [sid]


def test_token_only_reaches_its_own_accounts_sites(client, monkeypatch):
    sign_in(client, monkeypatch, BOB)
    bobs = upload(client, {}).json()["site"]["id"]
    h = bearer(new_token(client, monkeypatch, ALICE)["secret"])
    assert upload(client, h, url=f"/api/sites/{bobs}/versions").status_code == 403
    assert client.delete(f"/api/sites/{bobs}", headers=h).status_code == 403


def test_tokens_are_managed_only_from_a_browser_session(client, monkeypatch):
    assert client.post("/api/me/tokens", json={}).status_code == 401
    h = bearer(new_token(client, monkeypatch, ALICE)["secret"])
    # a token can't mint, list or revoke tokens
    assert client.post("/api/me/tokens", json={}, headers=h).status_code == 401
    assert client.get("/api/me/tokens", headers=h).status_code == 401
    sign_in(client, monkeypatch, ALICE)
    r = client.post("/api/me/tokens", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_list_and_revoke(client, monkeypatch):
    body = new_token(client, monkeypatch, ALICE, name="ci")
    h, tid = bearer(body["secret"]), body["token"]["id"]
    sign_in(client, monkeypatch, ALICE)
    listed = client.get("/api/me/tokens").json()["tokens"]
    assert [(t["id"], t["name"]) for t in listed] == [(tid, "ci")]
    assert "secret" not in listed[0] and "token_hash" not in listed[0]

    sign_in(client, monkeypatch, BOB)
    assert client.delete(f"/api/me/tokens/{tid}").status_code == 404  # not Bob's

    sign_in(client, monkeypatch, ALICE)
    assert client.delete(f"/api/me/tokens/{tid}").status_code == 200
    client.cookies.clear()
    r = upload(client, h)
    assert r.status_code == 401 and "revoked" in r.json()["error"]


def test_only_the_hash_is_stored(client, monkeypatch, tmp_path):
    secret = new_token(client, monkeypatch, ALICE)["secret"]
    db = sqlite3.connect(tmp_path / "t.db").execute("select token_hash, hint from api_tokens").fetchall()
    assert len(db) == 1 and secret not in db[0][0] and len(db[0][0]) == 64


def test_last_used_is_recorded(client, monkeypatch):
    h = bearer(new_token(client, monkeypatch, ALICE)["secret"])
    client.get("/api/me/sites", headers=h)
    sign_in(client, monkeypatch, ALICE)
    assert client.get("/api/me/tokens").json()["tokens"][0]["last_used_at"]


def test_token_never_grants_admin(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    sign_in(client, monkeypatch, ALICE)
    alices = upload(client, {}).json()["site"]["id"]
    h = bearer(new_token(client, monkeypatch, ADMIN)["secret"])
    assert client.get("/api/admin/reports", headers=h).status_code == 403
    assert client.delete(f"/api/sites/{alices}", headers=h).status_code == 403  # no takedown by token
    assert client.post("/api/admin/bans", json={"email": "alice@example.com"}, headers=h).status_code == 403


def test_banned_account_tokens_cannot_share(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    h = bearer(new_token(client, monkeypatch, ALICE)["secret"])
    sign_in(client, monkeypatch, ADMIN)
    assert client.post("/api/admin/bans", json={"email": "alice@example.com"}).status_code == 200
    client.cookies.clear()
    assert upload(client, h).status_code == 403
    sign_in(client, monkeypatch, ALICE)
    assert client.post("/api/me/tokens", json={}).status_code == 403


def test_token_limit(client, monkeypatch):
    monkeypatch.setattr(api_tokens, "MAX_TOKENS", 2)
    sign_in(client, monkeypatch, ALICE)
    assert client.post("/api/me/tokens", json={}).status_code == 200
    assert client.post("/api/me/tokens", json={}).status_code == 200
    assert client.post("/api/me/tokens", json={}).status_code == 409


def test_unknown_token_is_401_and_edit_tokens_still_work(client, monkeypatch):
    assert upload(client, bearer(api_tokens.PREFIX + "nope")).status_code == 401
    sign_in(client, monkeypatch, ALICE)
    body = upload(client, {}).json()
    client.cookies.clear()
    sid, edit = body["site"]["id"], body["edit_token"]
    assert upload(client, bearer(edit), url=f"/api/sites/{sid}/versions").status_code == 200
