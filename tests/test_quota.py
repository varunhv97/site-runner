import sqlite3

import _core.sites as core
from test_api import make_zip, page
from test_auth import ALICE, BOB, client, sign_in  # noqa: F401 (client is a fixture)

QUOTA = 4096  # bytes; 1/256 MB is exact in binary, so the env value converts without rounding


def upload(files: dict[str, str]):
    return {"file": ("s.zip", make_zip(files))}


def share(client, files):
    return client.post("/api/sites", files=upload(files))


def usage(client):
    r = client.get("/api/me/usage")
    assert r.status_code == 200, r.text
    return r.json()


def test_usage_requires_sign_in_and_has_default_quota(client, monkeypatch):
    monkeypatch.delenv("STORAGE_QUOTA_MB", raising=False)
    assert client.get("/api/me/usage").status_code == 401
    sign_in(client, monkeypatch, ALICE)
    assert usage(client) == {"used_bytes": 0, "quota_bytes": 250 * 1024 * 1024}
    sid = share(client, {"index.html": "a" * 100}).json()["site"]["id"]
    assert usage(client)["used_bytes"] == 100
    client.delete(f"/api/sites/{sid}")
    assert usage(client)["used_bytes"] == 0  # unpublished sites don't count


def test_quota_counts_shared_files_once_and_refuses_over_quota(client, monkeypatch):
    monkeypatch.setenv("STORAGE_QUOTA_MB", str(QUOTA / 1024 / 1024))
    sign_in(client, monkeypatch, ALICE)
    base = {"index.html": "a" * 1500, "style.css": "b" * 1500}
    sid = share(client, base).json()["site"]["id"]
    assert usage(client) == {"used_bytes": 3000, "quota_bytes": QUOTA}

    # same files again (another version, another site, a copy under another path): no new bytes
    assert client.post(f"/api/sites/{sid}/versions", files=upload(base)).status_code == 200
    assert share(client, base | {"copy.css": "b" * 1500}).status_code == 200
    assert usage(client)["used_bytes"] == 3000

    r = client.post(f"/api/sites/{sid}/versions", files=upload(base | {"big.js": "c" * 1200}))
    assert r.status_code == 413
    message = r.json()["error"]
    assert message.startswith("This would use ") and message.endswith("Delete old versions or sites to make room.")
    assert share(client, {"index.html": "d" * 1200}).status_code == 413
    assert usage(client)["used_bytes"] == 3000
    assert len(client.get(f"/api/sites/{sid}").json()["site"]["versions"]) == 2  # nothing was added

    assert client.post(f"/api/sites/{sid}/versions", files=upload(base | {"ok.js": "c" * 1000})).status_code == 200
    assert usage(client)["used_bytes"] == 4000


def test_quota_applies_to_owner_when_a_token_holder_publishes(client, monkeypatch):
    monkeypatch.setenv("STORAGE_QUOTA_MB", str(QUOTA / 1024 / 1024))
    sign_in(client, monkeypatch, ALICE)
    body = share(client, {"index.html": "a" * 3000}).json()
    client.cookies.clear()
    r = client.post(
        f"/api/sites/{body['site']['id']}/versions",
        files=upload({"index.html": "z" * 2000}),
        headers={"Authorization": f"Bearer {body['edit_token']}"},
    )
    assert r.status_code == 413


def test_sites_without_owner_are_not_quota_checked(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api.index as server
    from _core.db import make_engine
    from _core.storage import LocalStorage

    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STORAGE_QUOTA_MB", "0.0001")
    store = core.Sites(LocalStorage(str(tmp_path)), make_engine(f"sqlite:///{tmp_path}/t.db"))
    monkeypatch.setattr(server, "sites", store)
    r = TestClient(server.app).post("/api/sites", files=upload({"index.html": "a" * 5000}))
    assert r.status_code == 200


def test_sizes_of_older_files_are_backfilled_from_storage(client, monkeypatch, tmp_path):
    sign_in(client, monkeypatch, ALICE)
    share(client, {"index.html": "a" * 700, "a.css": "b" * 300})
    db = sqlite3.connect(tmp_path / "t.db")
    db.execute("DELETE FROM blobs")  # as if stored before the blobs table existed
    db.commit()
    assert usage(client)["used_bytes"] == 1000
    assert sorted(r[0] for r in db.execute("SELECT size FROM blobs")) == [300, 700]
    db.close()


def test_delete_version(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    body = share(client, {"index.html": "v1"}).json()
    sid, token = body["site"]["id"], body["edit_token"]
    for text in ("v2", "v3"):
        assert client.post(f"/api/sites/{sid}/versions", files=upload({"index.html": text})).status_code == 200

    r = client.delete(f"/api/sites/{sid}/versions/3")
    assert r.status_code == 409 and "live" in r.json()["error"]
    r = client.delete(f"/api/sites/{sid}/versions/2")
    assert r.status_code == 200
    assert [v["n"] for v in r.json()["site"]["versions"]] == [1, 3]
    assert client.get(f"/s/{sid}@2/").status_code == 404
    assert client.get(f"/r/{sid}@2/").status_code == 404
    assert page(client, f"/s/{sid}@1/") == "v1"
    assert page(client, f"/s/{sid}/") == "v3"
    assert client.delete(f"/api/sites/{sid}/versions/2").status_code == 404

    # the highest number is deleted too: new versions still continue after it
    client.post(f"/api/sites/{sid}/live", json={"version": 1})
    assert client.delete(f"/api/sites/{sid}/versions/3").status_code == 200
    r = client.post(f"/api/sites/{sid}/versions", files=upload({"index.html": "v4"}))
    assert r.json()["site"]["live"] == 4
    assert client.get(f"/s/{sid}@3/").status_code == 404

    sign_in(client, monkeypatch, BOB)
    assert client.delete(f"/api/sites/{sid}/versions/1").status_code == 403
    client.cookies.clear()
    assert client.delete(f"/api/sites/{sid}/versions/1").status_code == 403
    auth = {"Authorization": f"Bearer {token}"}
    assert client.delete(f"/api/sites/{sid}/versions/1", headers=auth | {"Origin": "null"}).status_code == 403
    assert client.delete(f"/api/sites/{sid}/versions/1", headers=auth).status_code == 200
    assert [v["n"] for v in client.get(f"/api/sites/{sid}").json()["site"]["versions"]] == [4]


def test_deleting_versions_frees_room_under_the_version_cap(client, monkeypatch):
    monkeypatch.setattr(core, "MAX_VERSIONS_PER_SITE", 2)
    sign_in(client, monkeypatch, ALICE)
    sid = share(client, {"index.html": "v1"}).json()["site"]["id"]
    assert client.post(f"/api/sites/{sid}/versions", files=upload({"index.html": "v2"})).status_code == 200
    assert client.post(f"/api/sites/{sid}/versions", files=upload({"index.html": "v3"})).status_code == 429
    assert client.delete(f"/api/sites/{sid}/versions/1").status_code == 200
    r = client.post(f"/api/sites/{sid}/versions", files=upload({"index.html": "v3"}))
    assert r.status_code == 200 and r.json()["site"]["live"] == 3


def test_deleted_version_frees_quota(client, monkeypatch):
    monkeypatch.setenv("STORAGE_QUOTA_MB", str(QUOTA / 1024 / 1024))
    sign_in(client, monkeypatch, ALICE)
    sid = share(client, {"index.html": "a" * 2000}).json()["site"]["id"]
    assert client.post(f"/api/sites/{sid}/versions", files=upload({"index.html": "b" * 2000})).status_code == 200
    assert client.post(f"/api/sites/{sid}/versions", files=upload({"index.html": "c" * 2000})).status_code == 413
    assert client.delete(f"/api/sites/{sid}/versions/1").status_code == 200
    assert usage(client)["used_bytes"] == 2000
    assert client.post(f"/api/sites/{sid}/versions", files=upload({"index.html": "c" * 2000})).status_code == 200


def test_cleanup_drops_size_rows_of_deleted_files_and_fills_missing_ones(client, monkeypatch, tmp_path):
    import os
    import time

    sign_in(client, monkeypatch, ALICE)
    keep = share(client, {"index.html": "k" * 10}).json()["site"]["id"]
    gone = share(client, {"index.html": "g" * 20}).json()["site"]["id"]
    client.delete(f"/api/sites/{gone}")
    old = time.time() - 7200
    for f in (tmp_path / "blobs").iterdir():
        os.utime(f, (old, old))
    db = sqlite3.connect(tmp_path / "t.db")
    db.execute("DELETE FROM blobs WHERE size = 10")  # an older file without a size row
    db.commit()
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    r = client.get("/api/cron/cleanup", headers={"Authorization": "Bearer s3cret"})
    assert r.json()["files_deleted"] == 1
    assert [row[0] for row in db.execute("SELECT size FROM blobs")] == [10]
    db.close()
    assert page(client, f"/s/{keep}/") == "k" * 10
