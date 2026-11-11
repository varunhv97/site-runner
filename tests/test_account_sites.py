import hashlib

from test_api import make_zip
from test_auth import ALICE, BOB, client, sign_in  # noqa: F401  (client is a fixture)

PAGE = "<h1>raw</h1><script>alert(1)</script>"


def share_as(client, monkeypatch, user, files):
    sign_in(client, monkeypatch, user)
    r = client.post("/api/sites", files={"file": ("s.zip", make_zip(files))}, data={"name": "mine"})
    assert r.status_code == 200, r.text
    return r.json()


def test_owner_takes_a_shared_version_back(client, monkeypatch):
    body = share_as(client, monkeypatch, ALICE, {"home.html": PAGE, "a.css": "p{}"})
    sid = body["site"]["id"]
    listed = client.get("/api/me/sites").json()["sites"]
    assert [s["id"] for s in listed] == [sid]

    v = client.get(f"/api/sites/{sid}/versions/1/files").json()
    assert v["entry"] == "home.html" and set(v["files"]) == {"home.html", "a.css"}
    r = client.get(f"/api/sites/{sid}/files/{v['files']['home.html']}")
    assert r.status_code == 200 and r.text == PAGE  # the original bytes
    # untrusted content: a download that can never render on the app's origin
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"] == "attachment"
    assert r.headers["content-security-policy"].startswith("sandbox")


def test_only_the_owner_or_token_can_download(client, monkeypatch):
    body = share_as(client, monkeypatch, ALICE, {"index.html": PAGE})
    sid, token = body["site"]["id"], body["edit_token"]
    digest = hashlib.sha256(PAGE.encode()).hexdigest()
    sign_in(client, monkeypatch, BOB)
    assert client.get(f"/api/sites/{sid}/versions/1/files").status_code == 403
    assert client.get(f"/api/sites/{sid}/files/{digest}").status_code == 403
    client.cookies.clear()
    assert client.get(f"/api/sites/{sid}/files/{digest}").status_code == 403
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/api/sites/{sid}/files/{digest}", headers=auth).text == PAGE


def test_files_of_other_sites_and_missing_versions(client, monkeypatch):
    mine = share_as(client, monkeypatch, ALICE, {"index.html": "mine"})["site"]["id"]
    share_as(client, monkeypatch, BOB, {"index.html": "bob's secret"})
    sign_in(client, monkeypatch, ALICE)
    bobs = hashlib.sha256(b"bob's secret").hexdigest()
    assert client.get(f"/api/sites/{mine}/files/{bobs}").status_code == 404  # not a file of this site
    assert client.get(f"/api/sites/{mine}/versions/7/files").status_code == 404
