import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import api.index as server
from _core.sites import Sites  # same module objects api.index uses
from _core.storage import LocalStorage


def make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "sites", Sites(LocalStorage(str(tmp_path))))
    return TestClient(server.app)


def create(client, files, name="demo"):
    r = client.post("/api/sites", files={"file": ("s.zip", make_zip(files))}, data={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_and_serve(client):
    body = create(client, {"site/index.html": "<h1>v1</h1>", "site/css/a.css": "h1{}"})
    sid = body["site"]["id"]
    assert body["url"].endswith(f"/s/{sid}/")

    r = client.get(f"/s/{sid}/")
    assert r.text == "<h1>v1</h1>"  # top-level folder stripped
    assert "sandbox" in r.headers["content-security-policy"]
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
    assert client.get(f"/s/{sid}/").text == "v2"
    assert client.get(f"/s/{sid}@1/").text == "v1"

    client.post(f"/api/sites/{sid}/live", json={"version": 1}, headers=auth)
    assert client.get(f"/s/{sid}/").text == "v1"
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
    assert client.get(f"/s/{sid}/").text == "ok"
    assert client.get(f"/s/{sid}/css/a.css").status_code == 200
    for hidden in (".env", ".git/config", "css/.DS_Store"):
        assert client.get(f"/s/{sid}/{hidden}").status_code == 404
