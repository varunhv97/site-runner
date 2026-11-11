import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient

import api.index as server
from _core.sites import Sites  # same module objects api.index uses
from _core.db import make_engine
from _core.storage import LocalStorage


def make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def page(client, url: str) -> str:
    """The HTML file a /s/ link shows: fetched from its /r/ frame, with the original page unwrapped."""
    r = client.get(url.replace("/s/", "/r/", 1))
    assert r.status_code == 200, r.text
    return json.loads(re.search(r"const PAGE = (.*);\n", r.text).group(1))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "sites", Sites(LocalStorage(str(tmp_path)), make_engine(f"sqlite:///{tmp_path}/t.db")))
    return TestClient(server.app)


def create(client, files, name="demo"):
    r = client.post("/api/sites", files={"file": ("s.zip", make_zip(files))}, data={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_and_serve(client):
    body = create(client, {"site/index.html": "<h1>v1</h1>", "site/css/a.css": "h1{}"})
    sid = body["site"]["id"]
    assert body["url"].endswith(f"/s/{sid}/")

    assert page(client, f"/s/{sid}/") == "<h1>v1</h1>"  # top-level folder stripped
    assert "sandbox" in client.get(f"/r/{sid}/").headers["content-security-policy"]
    assert client.get(f"/s/{sid}/css/a.css").headers["content-type"].startswith("text/css")
    assert client.get(f"/s/{sid}/missing.js").status_code == 404

    r = client.get(f"/s/{sid}", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == f"/s/{sid}/"


def test_versions_and_rollback(client):
    body = create(client, {"index.html": "v1"})
    sid, token = body["site"]["id"], body["edit_token"]
    auth = {"Authorization": f"Bearer {token}"}

    r = client.post(
        f"/api/sites/{sid}/versions", files={"file": ("s.zip", make_zip({"index.html": "v2"}))}, headers=auth
    )
    assert r.json()["site"]["live"] == 2
    assert page(client, f"/s/{sid}/") == "v2"
    assert page(client, f"/s/{sid}@1/") == "v1"

    client.post(f"/api/sites/{sid}/live", json={"version": 1}, headers=auth)
    assert page(client, f"/s/{sid}/") == "v1"
    assert client.get(f"/s/{sid}@0/").status_code == 404
    assert client.get(f"/s/{sid}@9/").status_code == 404


def test_requires_token(client):
    sid = create(client, {"index.html": "v1"})["site"]["id"]
    zip_bytes = make_zip({"index.html": "evil"})
    r = client.post(f"/api/sites/{sid}/versions", files={"file": ("s.zip", zip_bytes)})
    assert r.status_code == 403
    r = client.post(
        f"/api/sites/{sid}/versions", files={"file": ("s.zip", zip_bytes)}, headers={"Authorization": "Bearer nope"}
    )
    assert r.status_code == 403


def test_entry_fallback_and_dir_redirect(client):
    sid = create(client, {"pages/home.html": "home", "docs/index.html": "docs", "readme.txt": "x"})["site"]["id"]
    r = client.get(f"/s/{sid}/", follow_redirects=False)
    assert r.headers["location"] == f"/s/{sid}/docs/index.html"
    r = client.get(f"/s/{sid}/docs", follow_redirects=False)
    assert r.headers["location"] == f"/s/{sid}/docs/"


def test_rejects_bad_uploads(client):
    assert client.post("/api/sites", files={"file": ("s.zip", b"not a zip")}).status_code == 400
    assert client.post("/api/sites", files={"file": ("s.zip", make_zip({"a.txt": "x"}))}).status_code == 400
    r = client.post("/api/sites", files={"file": ("s.zip", make_zip({"../evil.html": "x"}))})
    assert r.status_code == 400


def test_production_refuses_anonymous_shares(client, monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    r = client.post("/api/sites", files={"file": ("s.zip", make_zip({"index.html": "x"}))})
    assert r.status_code == 503


def test_hidden_files_are_skipped(client):
    files = {"index.html": "ok", ".env": "SECRET=1", ".git/config": "[core]", "css/.DS_Store": "x", "css/a.css": "h1{}"}
    sid = create(client, files)["site"]["id"]
    assert page(client, f"/s/{sid}/") == "ok"
    assert client.get(f"/s/{sid}/css/a.css").status_code == 200
    for hidden in (".env", ".git/config", "css/.DS_Store"):
        assert client.get(f"/s/{sid}/{hidden}").status_code == 404


def test_cleanup_deletes_only_unused_old_files(client, tmp_path, monkeypatch):
    import os
    import time

    keep = create(client, {"index.html": "kept"})["site"]["id"]
    gone = create(client, {"index.html": "gone", "only-here.css": "x"})
    gone_id = gone["site"]["id"]
    client.delete(f"/api/sites/{gone_id}", headers={"Authorization": f"Bearer {gone['edit_token']}"})
    blobs = tmp_path / "blobs"
    old = time.time() - 7200
    for f in blobs.iterdir():
        os.utime(f, (old, old))
    (blobs / "fresh-upload").write_bytes(b"in flight")  # young: must survive

    assert client.get("/api/cron/cleanup").status_code == 401
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    assert client.get("/api/cron/cleanup", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = client.get("/api/cron/cleanup", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200 and r.json()["files_deleted"] == 2  # "gone" and "only-here.css"
    assert page(client, f"/s/{keep}/") == "kept"
    assert (blobs / "fresh-upload").exists()


def test_blob_storage_credentials(monkeypatch):
    from _core.storage import BlobStorage, oidc_token

    rw = BlobStorage(token="vercel_blob_rw_AbC123_secret")
    assert rw.base == "https://abc123.private.blob.vercel-storage.com"
    assert "x-vercel-blob-store-id" not in rw._auth()

    monkeypatch.delenv("VERCEL_OIDC_TOKEN", raising=False)
    oidc = BlobStorage(store_id="store_S7RKoWvKj")
    assert oidc.base == "https://s7rkowvkj.private.blob.vercel-storage.com"
    oidc_token.set("request-token")
    assert oidc._auth()["authorization"] == "Bearer request-token"
    assert oidc._auth()["x-vercel-blob-store-id"] == "S7RKoWvKj"
