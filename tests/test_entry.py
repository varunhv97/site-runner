from test_api import client, make_zip, page  # noqa: F401  (client is a fixture)

PAGES = {"about.html": "about", "home.html": "home", "style.css": "p{}"}


def share(client, files=PAGES, **data):
    r = client.post("/api/sites", files={"file": ("s.zip", make_zip(files))}, data={"name": "x", **data})
    return r


def test_without_a_choice_the_guess_is_unchanged(client):
    sid = share(client).json()["site"]["id"]
    assert page(client, f"/s/{sid}/") == "about"  # shallowest, then alphabetical


def test_the_chosen_start_page_is_used(client):
    sid = share(client, entry="home.html").json()["site"]["id"]
    assert page(client, f"/s/{sid}/") == "home"
    assert page(client, f"/s/{sid}@1/") == "home"


def test_only_html_files_of_the_upload_can_be_the_start_page(client):
    assert share(client, entry="nope.html").status_code == 400
    assert share(client, entry="style.css").status_code == 400
    assert share(client, entry="/home.html").status_code == 200  # a leading slash is fine


def test_new_versions_take_a_start_page_too(client):
    body = share(client).json()
    sid, auth = body["site"]["id"], {"Authorization": f"Bearer {body['edit_token']}"}
    v2 = {"file": ("s.zip", make_zip(PAGES))}
    r = client.post(f"/api/sites/{sid}/versions", files=v2, data={"entry": "home.html"}, headers=auth)
    assert r.status_code == 200 and page(client, f"/s/{sid}/") == "home"


def test_change_the_start_page_of_a_shared_version(client):
    body = share(client).json()
    sid, auth = body["site"]["id"], {"Authorization": f"Bearer {body['edit_token']}"}
    url = f"/api/sites/{sid}/versions/1/entry"
    assert client.post(url, json={"entry": "home.html"}).status_code == 403  # no token
    assert client.post(url, json={"entry": "nope.html"}, headers=auth).status_code == 400
    assert (
        client.post(f"/api/sites/{sid}/versions/9/entry", json={"entry": "home.html"}, headers=auth).status_code == 404
    )
    assert client.post(url, json={"entry": "home.html"}, headers=auth).status_code == 200
    assert page(client, f"/s/{sid}/") == "home" and page(client, f"/s/{sid}@1/") == "home"


def test_a_pinned_start_page_is_not_cached_for_long(client):
    sid = share(client).json()["site"]["id"]
    assert client.get(f"/r/{sid}@1/").headers["cache-control"] == "no-cache"  # the owner can change it
    assert "max-age=3600" in client.get(f"/r/{sid}@1/home.html").headers["cache-control"]
